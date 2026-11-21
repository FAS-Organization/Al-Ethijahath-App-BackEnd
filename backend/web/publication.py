import hashlib
from django.core.files.base import ContentFile
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from customer.ledger import staff_region
from customer.pdf import render_pdf
from customer.sharing import audit
from .models import (DocumentTemplate, GeneratedDocument, InspectionReport, JobCompletionCard, Invoice, Quotation)


SOURCES={"inspection":(InspectionReport,"inspection_report"),"completion":(JobCompletionCard,"completion_card"),
         "quotation":(Quotation,"quotation"),"invoice":(Invoice,"invoice")}


@transaction.atomic
def publish(actor,kind,pk):
    if kind not in SOURCES:
        raise ValidationError("Unknown customer document type.")
    model,field=SOURCES[kind]
    source=get_object_or_404(model.objects.select_for_update(),pk=pk)
    machine=getattr(source,"job_equipment",None)
    job=machine.job if machine else source.job
    region=job.region
    staff_region(actor,"web.add_generateddocument",region.pk)
    if not actor.is_superuser and actor.groups.filter(name="Technician").exists():
        if kind not in ("completion","inspection") or source.technician.user_id!=actor.pk:
            raise PermissionDenied()
    valid={"inspection":{"pending_approval","approved"},"completion":{"pending_approval","approved"},"quotation":{"sent","approved","rejected","expired"},
           "invoice":{"issued","void"}}
    if source.status not in valid[kind] or getattr(source,"purpose","customer")!="customer":
        raise ValidationError("This version is not available for customer publication.")
    visibility="internal" if kind=="completion" and source.status=="pending_approval" else "customer"
    signature_ids = list(source.approvals.filter(decision="approved").order_by("pk").values_list("pk", flat=True)) if kind in ("inspection","completion") else []
    existing=source.pdfs.filter(status="ready",visibility=visibility,data_snapshot__source_status=source.status,
                               data_snapshot__signature_ids=signature_ids).order_by("-pk").first()
    if existing:
        return existing
    template=DocumentTemplate.objects.filter(is_active=True,document_type=kind).first()
    if not template:
        raise ValidationError("Configure an active document template for this type in Django admin.")
    number=getattr(source,"number",None) or f"{'INS' if kind=='inspection' else 'JCC'}-{machine.reference}-V{source.version}"
    title=f"{template.get_document_type_display()} · {number}"
    bill=getattr(source,"billing_snapshot",{})
    paragraphs=[str(bill.get("name") or job.company.name),job.site_address_snapshot or job.site.address,
                f"Status: {source.get_status_display()}",f"Service: {machine.reference if machine else job.pk}"]
    if template.fixed_content.get("header"):
        paragraphs.insert(0,str(template.fixed_content["header"]))
    evidence=None
    signatures=[]
    if kind=="quotation":
        paragraphs += [source.customer_summary,source.estimated_completion_text,source.terms,
            f"Valid until: {source.expires_at}",f"Total: {source.currency} {source.total}"]
        rows=[["Description","Quantity","Unit price","Line total"]]+[
            [l.description,str(l.quantity),str(l.unit_price),str(l.line_total)] for l in source.lines.order_by("display_order","pk")]
    elif kind=="invoice":
        paragraphs += [f"Issued: {source.issue_date}",f"Due: {source.due_date}",
            f"Subtotal: {source.subtotal}   Discount: {source.discount}   Tax: {source.tax_amount}",f"Total: {source.currency} {source.total}"]
        rows=[["Description","Quantity","Unit price","Line total"]]+[
            [l.description,str(l.quantity),str(l.unit_price),str(l.line_total)] for l in source.lines.order_by("display_order","pk")]
    elif kind=="inspection":
        paragraphs += [source.remarks]
        rows=[["Component","Condition","Proposed action","Finding"]]+[
            [a.component,a.condition,a.proposed_action,a.description] for a in source.findings.order_by("display_order","pk")]
    else:
        paragraphs += [source.work_summary,source.final_condition,source.out_of_scope_notes]
        rows=[["Component","Work performed","Action","Quantity"]]+[
            [a.component,a.work_performed,a.action_taken,str(a.quantity_used)] for a in source.work_items.order_by("pk")]
    if kind in ("inspection","completion"):
        signs=list(source.approvals.filter(decision="approved").select_related("email_submission"))
        if source.status=="approved" and {e.signer_role for e in signs}!={"technician","customer"}:
            raise ValidationError("Both signatures for this version are required.")
        for e in signs:
            if e.method=="email_attachment" and e.email_submission_id:
                evidence=e.email_submission
            elif e.method=="on_site" and e.evidence_file:
                signatures.append((f"{e.get_signer_role_display()}: {e.signer_name}",e.evidence_file))
    if template.fixed_content.get("footer"):
        paragraphs.append(str(template.fixed_content["footer"]))
    if evidence:
        with evidence.signed_pdf.open("rb") as fp:
            data=fp.read()
        if hashlib.sha256(data).hexdigest()!=evidence.signed_pdf_sha256:
            raise ValidationError("Returned signed PDF differs from the verified evidence.")
    else:
        data=render_pdf(title,paragraphs,rows,signatures)
    now=timezone.now()
    result=GeneratedDocument(**{field:source},document_number=number,template=template,created_by=actor,visibility=visibility,status="ready",
        data_snapshot={"source_status":source.status,"signature_ids":signature_ids,"source_id":pk,"version":getattr(source,"version",1),"paragraphs":paragraphs,"rows":rows},
        generated_at=now,published_at=now,sha256=hashlib.sha256(data).hexdigest())
    result.full_clean()
    result.file.save("document.pdf",ContentFile(data),save=False)
    try:
        result.save()
    except Exception:
        result.file.delete(save=False)
        raise
    audit(actor,"document.published",result,source_id=pk,sha256=result.sha256)
    return result


@api_view(["POST"])
@permission_classes([IsAdminUser])
def publish_view(request,kind,pk):
    result=publish(request.user,kind,pk)
    return Response({"id":result.pk,"number":result.document_number,"sha256":result.sha256})
