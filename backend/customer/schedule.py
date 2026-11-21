from zoneinfo import ZoneInfo
from django.db import transaction, IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from web.models import AMCEquipmentCoverage, AMCMonthlySummary, JobEquipment, JobCompletionCard, RescheduleRequest, ServiceAppointment
from .documents import file_url
from .ledger import staff_region
from .permissions import IsCustomer
from .records import fields, inspections_for, quotes_for, visit_row
from .sharing import audit, value

OPEN=("pending","alternative")


def row_data(r):
    m=r.appointment.job_equipment
    return {**fields(r,"id","appointment_id","original_start","reason","alternative_date"),"reference":m.reference,
            "equipment_id":m.equipment_id,"requested_date":r.proposed_date,"submitted_at":r.created_at,"responded_at":r.reviewed_at,
            "notes":r.decision_notes,"alternative_note":r.decision_notes,"customer_response":"accepted" if r.alternative_accepted_at else None,
            "status":"alternative_proposed" if r.status=="alternative" else r.status}


@api_view(["GET"])
@permission_classes([IsCustomer])
def schedule(request):
    c=request.user.customer_company
    contracts=c.amc_contracts.exclude(status="draft")
    machines=JobEquipment.objects.filter(job__company=c,equipment__company=c)
    visits=ServiceAppointment.objects.filter(job_equipment__in=machines).select_related(
        "job_equipment__job","technician__user").prefetch_related("reschedule_requests").order_by("scheduled_start")
    approved=set(JobCompletionCard.objects.filter(job_equipment__in=machines,status="approved").values_list("job_equipment_id",flat=True))
    appointments,requests,last=[],[],{}
    for v in visits:
        appointments.append({**visit_row(v),"has_completion_card":v.job_equipment_id in approved})
        requests.extend(row_data(r) for r in v.reschedule_requests.all())
        if v.purpose=="maintenance" and v.status=="completed" and v.job_equipment.amc_coverage_id:
            last[v.job_equipment.amc_coverage_id]=v.actual_end or v.scheduled_end
    monthly=[]
    for s in AMCMonthlySummary.objects.filter(contract__in=contracts,published_at__lte=timezone.now()).select_related("contract"):
        monthly.append({**fields(s,"id","month_start","summary"),"contract_number":s.contract.contract_number,"completed_count":len(s.visits),
            "equipment":list({v["equipment"]["id"]:v["equipment"] for v in s.visits}.values()),"services":[{"reference":v["reference"]} for v in s.visits],
            "site_name":", ".join(sorted({v["site"] for v in s.visits})),"pdf_url":file_url("monthly",s.pk) if s.file else None})
    return Response({"is_demo":False,"now":timezone.now(),"company_name":c.name,"sites":[{**fields(s,"id","name","address"),"region":s.region.name}
            for s in c.sites.filter(is_active=True).select_related("region")],
        "equipment":[{"id":e.pk,"name":str(e),"site_id":e.current_site_id,"asset":e.customer_asset_number,"serial":e.serial_number,"is_retired":e.is_retired} for e in c.equipment.all()],
        "contracts":[{**fields(a,"id","status","contract_number","start_date","end_date"),"interval_months":a.service_interval_months,
                      "pdf_url":file_url("contract",a.pk) if a.contract_file else None} for a in contracts],
        "coverages":[{**fields(v,"id","contract_id","equipment_id","start_date","end_date","interval_months_override","is_active","next_service_due_date"),
                      "last_completed_service_date":last.get(v.pk)} for v in AMCEquipmentCoverage.objects.filter(contract__in=contracts,equipment__company=c)],
        "appointments":appointments,"reschedule_requests":requests,"monthly_summaries":monthly,
        "related_services":[{**fields(m,"equipment_id","reference","status","completed_at","started_at"),"site_id":m.job.site_id,
            "date":m.completed_at or m.started_at or m.created_at,"job_type":m.job.job_type,"is_amc":bool(m.amc_coverage_id)} for m in machines.select_related("job")],
        "invoices":[{"id":i.pk,"date":i.issue_date,"total":str(i.total),"invoiced":str(i.total),"outstanding":str(i.balance_due),
            "credited":str(i.amount_credited),"paid":str(i.amount_paid),"currency":i.currency} for i in c.invoices.filter(status="issued")],
        "quotations":[{"id":q["id"],"date":q["sent_at"],"status":"pending" if q["status"]=="sent" else q["status"],"currency":q["currency"],"amount":q["total"]}
                      for q in quotes_for(c) if q["status"]!="superseded"],
        "inspections":[{"id":i["id"],"date":i["inspected_at"],"status":"completed" if i["status"]=="approved" else "pending"}
                       for i in inspections_for(c) if i["status"]!="superseded"],
        "job_cards":[{"id":r.pk,"date":r.completed_at,"status":"completed" if r.status=="approved" else "pending"}
                     for r in JobCompletionCard.objects.filter(job_equipment__in=machines,status__in=["approved","pending_approval"])]})


@api_view(["POST"])
@permission_classes([IsCustomer])
@transaction.atomic
def request_date(request,pk):
    v=get_object_or_404(ServiceAppointment.objects.select_for_update(),pk=pk,job_equipment__job__company=request.user.customer_company)
    if v.status not in ("planned","confirmed") or v.scheduled_start<=timezone.now() or v.reschedule_requests.filter(status__in=OPEN).exists():
        raise ValidationError("This visit already has a request or cannot be rescheduled.")
    d=value(serializers.DateField(),request.data,"date")
    reason=value(serializers.CharField(max_length=2000),request.data,"reason")
    tz=ZoneInfo(v.job_equipment.job.region.timezone)
    if d<=timezone.localdate(timezone=tz) or d==timezone.localdate(v.scheduled_start,timezone=tz):
        raise ValidationError("Choose a different future date.")
    r=RescheduleRequest.objects.create(appointment=v,requested_by=request.user,created_by=request.user,original_start=v.scheduled_start,
        original_end=v.scheduled_end,appointment_version=v.schedule_version,proposed_date=d,reason=reason)
    audit(request.user,"visit.reschedule_requested",r)
    return Response(row_data(r),status=201)


@api_view(["POST"])
@permission_classes([IsCustomer])
@transaction.atomic
def respond(request,pk):
    r=get_object_or_404(RescheduleRequest.objects.select_for_update(),pk=pk,appointment__job_equipment__job__company=request.user.customer_company)
    if r.status not in OPEN:
        raise ValidationError("This request is closed.")
    action=request.data.get("action")
    if action=="withdraw":
        r.status="withdrawn"
    elif (action=="accept_alternative" and r.status=="alternative" and r.alternative_date and
          r.alternative_date>timezone.localdate(timezone=ZoneInfo(r.appointment.job_equipment.job.region.timezone)) and r.appointment.schedule_version==r.appointment_version):
        r.alternative_accepted_at=r.alternative_accepted_at or timezone.now()
    else:
        raise ValidationError("Choose an available action.")
    r.save(update_fields=["status","alternative_accepted_at","updated_at"])
    audit(request.user,"visit."+action,r)
    return Response(row_data(r))


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def review(request,pk):
    r=get_object_or_404(RescheduleRequest.objects.select_for_update(),pk=pk)
    v=ServiceAppointment.objects.select_for_update().get(pk=r.appointment_id)
    region=v.job_equipment.job.region
    staff_region(request.user,"web.change_reschedulerequest",region.pk)
    if r.status not in OPEN or r.appointment_version!=v.schedule_version:
        raise ValidationError("The request is closed or the schedule has changed.")
    action=value(serializers.ChoiceField(choices=["alternative","confirm","reject"]),request.data,"action")
    tz=ZoneInfo(region.timezone)
    if action=="alternative":
        date=value(serializers.DateField(),request.data,"date")
        if date<=timezone.localdate(timezone=tz):
            raise ValidationError("Choose a future date.")
        r.status,r.alternative_date,r.alternative_accepted_at="alternative",date,None
    elif action=="confirm":
        if r.status=="alternative" and not r.alternative_accepted_at:
            raise ValidationError("Customer acceptance of the alternative date is required.")
        date=r.alternative_date if r.status=="alternative" else r.proposed_date
        start=value(serializers.DateTimeField(),request.data,"start")
        end=value(serializers.DateTimeField(),request.data,"end")
        if (start<=timezone.now() or end<=start or timezone.localdate(start,timezone=tz)!=date or v.status not in ("planned","confirmed")):
            raise ValidationError("Confirm an exact future time on the agreed date.")
        v.scheduled_start,v.scheduled_end,v.status=start,end,"confirmed"
        v.schedule_version+=1
        v.save(update_fields=["scheduled_start","scheduled_end","status","schedule_version","updated_at"])
        r.status="accepted"
    else:
        r.status="rejected"
    r.reviewed_at,r.reviewed_by,r.decision_notes=timezone.now(),request.user,serializers.CharField(max_length=2000,allow_blank=True).run_validation(request.data.get("notes",""))
    r.save()
    audit(request.user,"visit.reviewed",r,decision=r.status)
    return Response(row_data(r))
