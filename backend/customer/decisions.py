from pathlib import Path
from django.core.exceptions import ValidationError as ModelError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from web.models import DocumentApproval, EmailApprovalSubmission, InspectionReport, JobCompletionCard, JobEquipment, QuoteEmailRequest, Quotation
from .documents import pdfs
from .ledger import staff_region
from .permissions import IsCustomer
from .sharing import audit, value


def checked(obj):
    try:
        obj.full_clean()
    except ModelError as exc:
        raise ValidationError(getattr(exc,"message_dict",exc.messages)) from exc
    obj.save()
    return obj


def quote_for(company, pk, version):
    q=get_object_or_404(Quotation.objects.select_for_update(),pk=pk,job_equipment__job__company=company)
    if (q.version!=version or q.status!="sent" or not q.expires_at or q.is_expired or
            Quotation.objects.filter(job_equipment_id=q.job_equipment_id,version__gt=q.version).exclude(status="draft").exists()):
        raise ValidationError("This quotation version is no longer open for a decision.")
    return q


def decide(actor,q,event,reason=""):
    checked(event)
    q.status,q.decided_at,q.rejection_reason=event.decision,timezone.now(),reason
    checked(q)
    if q.status=="approved":
        JobEquipment.objects.filter(pk=q.job_equipment_id,status__in=["quotation","awaiting_approval"]).update(status="quotation_approved")
    audit(actor,"quotation."+q.status,q,version=q.version,method=event.method)
    return Response({"id":q.pk,"status":q.status,"version":q.version,"decided_at":q.decided_at})


def pdf_upload(file):
    if file.size > 15*1024*1024 or Path(file.name).suffix.lower()!=".pdf":
        raise ValidationError("Choose a PDF up to 15 MB.")
    header=file.read(5)
    file.seek(0)
    if header!=b"%PDF-":
        raise ValidationError("The uploaded file is not a PDF.")
    return file


@api_view(["POST"])
@permission_classes([IsCustomer])
@transaction.atomic
def quote_action(request,pk,action):
    c=request.user.customer_company
    version=value(serializers.IntegerField(min_value=1),request.data,"version")
    q=quote_for(c,pk,version)
    pdf=pdfs(c).filter(quotation=q).order_by("-published_at","-pk").first()
    if action=="email":
        if not pdf:
            raise ValidationError("Publish this quotation PDF before requesting an email.")
        row,_=QuoteEmailRequest.objects.get_or_create(document=pdf,recipient=request.user.email,defaults={"created_by":request.user})
        return Response({"id":row.pk,"status":"queued","already_sent":bool(row.sent_at)},status=202)
    decision=value(serializers.ChoiceField(choices=["approved","rejected"]),request.data,"decision")
    attachment=""
    if decision=="approved":
        attachment=value(serializers.FileField(),request.data,"purchase_order")
        if Path(attachment.name).suffix.lower()==".pdf":
            pdf_upload(attachment)
        else:
            if attachment.size>10*1024*1024:
                raise ValidationError("Choose an image up to 10 MB.")
            attachment=serializers.ImageField().run_validation(attachment)
            if attachment.image.format not in {"JPEG","WEBP","PNG"}:
                raise ValidationError("Choose a PDF, JPG, PNG or WebP.")
    reason=serializers.CharField(max_length=2000,allow_blank=True).run_validation(request.data.get("reason",""))
    event=DocumentApproval(quotation=q,method="in_app",decision=decision,signer_role="customer",signer_name=c.contact_name,
        signer_email=request.user.email,signing_user=request.user,created_by=request.user,authority_basis="Authenticated company account holder",
        authority_snapshot={"company":c.pk,"holder":request.user.pk,"version":q.version},reviewed_document=pdf,
        document_sha256=pdf.sha256 if pdf else "",evidence_file=attachment,approved_at=timezone.now())
    return decide(request.user,q,event,reason if decision=="rejected" else "")


class Reply(serializers.Serializer):
    version=serializers.IntegerField(min_value=1)
    email_request=serializers.IntegerField(min_value=1)
    sender=serializers.EmailField()
    cc=serializers.EmailField()
    signer_name=serializers.CharField(max_length=150)
    role=serializers.CharField(max_length=100)
    purchase_order_note=serializers.CharField(max_length=500)
    email_evidence=serializers.FileField()


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def verify_reply(request,pk):
    form=Reply(data=request.data)
    form.is_valid(raise_exception=True)
    v=form.validated_data
    raw=get_object_or_404(Quotation,pk=pk)
    staff_region(request.user,"web.verify_quotation_reply",raw.region_id)
    c=raw.job_equipment.job.company
    q=quote_for(c,pk,v["version"])
    sent=get_object_or_404(QuoteEmailRequest,pk=v["email_request"],sent_at__isnull=False,document__quotation=q)
    if v["sender"].lower()!=sent.recipient.lower() or v["sender"].lower()!=c.account_holder.email.lower():
        raise ValidationError("Confirm that the reply came from the registered account email.")
    if v["cc"].lower()==v["sender"].lower() or v["email_evidence"].size>15*1024*1024:
        raise ValidationError("Include the copied purchasing contact or approver and email evidence up to 15 MB.")
    event=DocumentApproval(quotation=q,quote_email=sent,reviewed_document=sent.document,document_sha256=sent.document.sha256,
        method="quote_reply",decision="approved",signer_role="customer",signer_name=v["signer_name"],signer_email=v["sender"],
        signing_user=c.account_holder,created_by=request.user,authority_basis="Sales verified the registered account holder's acceptance",
        authority_snapshot={"role":v["role"],"cc":v["cc"],"purchase_order":v["purchase_order_note"],"version":q.version},
        evidence_file=v["email_evidence"],approved_at=timezone.now())
    return decide(request.user,q,event)


REPORTS={"inspection":(InspectionReport,"inspection_report"),"completion":(JobCompletionCard,"completion_card")}


def pending_report(kind,pk):
    from django.http import Http404
    if kind not in REPORTS:
        raise Http404
    model,field=REPORTS[kind]
    report=get_object_or_404(model.objects.select_for_update(),pk=pk)
    newer=model.objects.filter(job_equipment_id=report.job_equipment_id,version__gt=report.version).exclude(status="draft")
    if kind=="inspection":
        newer=newer.filter(purpose="customer")
    if (report.status!="pending_approval" or getattr(report,"purpose","customer")!="customer" or newer.exists()):
        raise ValidationError("Choose the current pending customer report.")
    return report,field


class Signatures(serializers.Serializer):
    signer_name=serializers.CharField(max_length=150)
    approver_id=serializers.IntegerField(min_value=1,required=False)
    customer_signature=serializers.ImageField()
    technician_signature=serializers.ImageField(required=False)


def finalize(actor,kind,report):
    report.status,report.finalized_at="approved",timezone.now()
    checked(report)
    audit(actor,kind+".approved",report,version=report.version)
    return Response({"id":report.pk,"version":report.version,"status":report.status})


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def tablet(request,kind,pk):
    report,field=pending_report(kind,pk)
    m=report.job_equipment
    staff_region(request.user,"web.change_"+report._meta.model_name,m.job.region_id)
    if report.technician.user_id!=request.user.pk or (report.assignment_id and (
            report.assignment.status!="accepted" or report.assignment.technician_id!=report.technician_id)) or (
            not report.assignment_id and m.assigned_technician_id!=report.technician_id):
        raise PermissionDenied("Only the technician assigned to this report can record the on-site approval.")
    form=Signatures(data=request.data)
    form.is_valid(raise_exception=True)
    v=form.validated_data
    for key in ["technician_signature","customer_signature"]:
        if key in v and v[key].size>5*1024*1024:
            raise ValidationError("Signature images must be 5 MB or smaller.")
    company=m.job.company
    signer=get_object_or_404(company.authorized_approvers,pk=v["approver_id"],is_active=True) if "approver_id" in v else None
    expected_name=signer.name if signer else company.contact_name
    if " ".join(v["signer_name"].split()).casefold() != " ".join(expected_name.split()).casefold():
        raise ValidationError({"signer_name":"Choose the account holder or a current authorized signer for this company."})
    common={field:report,"decision":"approved","approved_at":timezone.now(),"created_by":request.user,"method":"on_site"}
    if not report.approvals.filter(signer_role="technician",signing_user=request.user,decision="approved").exists():
        if not v.get("technician_signature"):
            raise ValidationError("Capture the technician signature for this revision.")
        checked(DocumentApproval(**common,signer_role="technician",signer_name=request.user.get_full_name() or request.user.email,
            signer_email=request.user.email,signing_user=request.user,authority_basis="Assigned technician",evidence_file=v["technician_signature"]))
    checked(DocumentApproval(**common,signer_role="customer",signer_name=v["signer_name"],customer_approver=signer,
        signer_email=signer.email if signer else company.account_holder.email,signing_user=None if signer else company.account_holder,
        authority_basis="Named authorized signer" if signer else "Company account holder",
        authority_snapshot={"company":company.pk,"version":report.version,"signer":signer.name if signer else company.contact_name},
        evidence_file=v["customer_signature"]))
    return finalize(request.user,kind,report)


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def verify_return(request,pk):
    row=get_object_or_404(EmailApprovalSubmission.objects.select_for_update(),pk=pk)
    doc=row.source_document
    kind="inspection" if doc.inspection_report_id else "completion"
    source=doc.inspection_report or doc.completion_card
    if not source:
        raise ValidationError("A signed service report PDF is required.")
    report,field=pending_report(kind,source.pk)
    staff_region(request.user,"web.verify_email_approval",report.job_equipment.job.region_id)
    if row.status!="pending" or (row.customer_approver_id and not row.customer_approver.is_active):
        raise ValidationError("The return or signer's authority is no longer current.")
    signer_email=row.customer_approver.email if row.customer_approver_id else row.signing_user.email
    if row.signer_email.lower()!=signer_email.lower():
        raise ValidationError("Check the signer's address against the registered authority.")
    if not report.approvals.filter(signer_role="technician",decision="approved",signing_user=report.technician.user).exists():
        raise ValidationError("Record the technician's signature on this exact report version first.")
    import hashlib
    with row.signed_pdf.open("rb") as returned:
        if returned.read(5)!=b"%PDF-":
            raise ValidationError("The full returned PDF is required.")
        returned.seek(0)
        sha=hashlib.sha256()
        for chunk in returned.chunks():
            sha.update(chunk)
    row.signed_pdf_sha256=sha.hexdigest()
    row.status,row.verified_by,row.verified_at="verified",request.user,timezone.now()
    checked(row)
    checked(DocumentApproval(**{field:report},decision="approved",method="email_attachment",email_submission=row,
        reviewed_document=doc,document_sha256=doc.sha256,approved_at=row.verified_at,signer_role="customer",
        signing_user=row.signing_user,customer_approver=row.customer_approver,signer_name=row.signer_name,signer_email=row.signer_email,
        created_by=request.user,authority_basis="Sales verified the returned signed PDF",authority_snapshot={"version":report.version,"sender":row.sender_email}))
    return finalize(request.user,kind,report)
