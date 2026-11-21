from pathlib import Path

from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from web.models import AMCContract, AMCMonthlySummary, GeneratedDocument, Region, ServiceAttachment
from .permissions import IsCustomer


def pdfs(company):
    return GeneratedDocument.objects.filter(
        Q(inspection_report__job_equipment__job__company=company,inspection_report__purpose="customer",
          inspection_report__status__in=["pending_approval","approved","superseded"]) |
        Q(completion_card__job_equipment__job__company=company,completion_card__status__in=["approved","superseded"]) |
        Q(quotation__job_equipment__job__company=company,quotation__status__in=["sent","approved","rejected","expired","superseded"]) |
        Q(invoice__company=company,invoice__status__in=["issued","void"]),
        visibility="customer",status="ready",published_at__lte=timezone.now(),internal_purchase_report=None).exclude(file="")


def attachments(company):
    return (ServiceAttachment.objects.filter(job_equipment__job__company=company,visibility="customer")
        .filter(Q(update=None)|Q(update__visibility="customer"))
        .filter(Q(finding=None)|Q(finding__report__purpose="customer",finding__report__status__in=["pending_approval","approved","superseded"]))
        .filter(Q(completion_card=None)|Q(completion_card__status__in=["approved","superseded"])).exclude(file=""))


def file_url(kind, pk):
    return reverse("portal-file",args=[kind,pk])


def archive(company):
    rows=[]
    for p in pdfs(company).select_related("inspection_report","completion_card","quotation","invoice","template").order_by("-published_at"):
        src=p.inspection_report or p.completion_card or p.quotation or p.invoice
        m=getattr(src,"job_equipment",None)
        job=m.job if m else src.job
        kind=p.template.document_type
        rows.append({"id":f"pdf-{p.pk}","type":kind,"number":p.document_number,"title":p.template.get_document_type_display(),
            "version":getattr(src,"version",1),"previous_version":src.status=="superseded","source_status":src.status,
            "date":p.published_at,"reference":m.reference if m else str(job.pk),"site_names":[job.site.name],
            "equipment":str(m.equipment) if m else "", "asset_number":m.equipment.customer_asset_number if m else "",
            "file_url":file_url("pdf",p.pk),"filename":p.document_number+".pdf","visibility":"customer","details":[]})
    for c in company.amc_contracts.exclude(status="draft"):
        rows.append({"id":f"contract-{c.pk}","type":"contract","number":c.contract_number,"title":"AMC contract",
            "reference":c.contract_number,"site_names":[],"date":c.start_date,"source_status":c.status,"visibility":"customer",
            "file_url":file_url("contract",c.pk) if c.contract_file else None,"details":[]})
    for a in attachments(company).select_related("job_equipment__job__site","job_equipment__equipment"):
        rows.append({"id":f"attachment-{a.pk}","type":"attachment","title":a.caption or "Service attachment",
            "reference":a.job_equipment.reference,"equipment":str(a.job_equipment.equipment),"site_names":[a.job_equipment.job.site.name],
            "date":a.created_at,"file_url":file_url("attachment",a.pk),"visibility":"customer","details":[]})
    for summary in AMCMonthlySummary.objects.filter(contract__company=company,published_at__lte=timezone.now()).exclude(
            contract__status="draft").exclude(file="").select_related("contract"):
        rows.append({"id":f"monthly-{summary.pk}","type":"monthly_summary","title":"AMC monthly summary",
            "number":summary.contract.contract_number,"reference":summary.contract.contract_number,
            "date":summary.month_start,"source_status":"published","visibility":"customer",
            "site_names":sorted({v["site"] for v in summary.visits}),"details":[],
            "file_url":file_url("monthly",summary.pk)})
    return rows


@api_view(["GET"])
@permission_classes([IsCustomer])
def documents(request):
    c=request.user.customer_company
    return Response({"company_name":c.name,"is_demo":False,"documents":archive(c)})


@api_view(["GET"])
@permission_classes([IsCustomer])
def download(request,kind,pk):
    c=request.user.customer_company
    if kind=="pdf":
        doc=get_object_or_404(pdfs(c),pk=pk)
        field,name=doc.file,doc.document_number+".pdf"
    elif kind=="attachment":
        field=get_object_or_404(attachments(c),pk=pk).file
        name="service-attachment"+Path(field.name).suffix
    elif kind=="contract":
        contract=get_object_or_404(c.amc_contracts.exclude(status="draft"),pk=pk)
        field,name=contract.contract_file,contract.contract_number+".pdf"
    elif kind=="monthly":
        row=get_object_or_404(AMCMonthlySummary.objects.filter(contract__company=c,published_at__lte=timezone.now()).exclude(contract__status="draft"),pk=pk)
        field,name=row.file,f"AMC-{row.month_start}.pdf"
    elif kind=="payment-qr":
        obj=get_object_or_404(Region.objects.filter(Q(pk=c.region_id)|Q(invoice__company=c,invoice__status="issued")).distinct(),pk=pk)
        field,name=obj.payment_qr,"payment-qr"+Path(obj.payment_qr.name or "").suffix
    else:
        raise Http404
    if not field:
        raise Http404
    try:
        response=FileResponse(field.open("rb"),as_attachment=True,filename=name)
    except FileNotFoundError as exc:
        raise Http404 from exc
    response["Cache-Control"]="private, no-store"
    return response
