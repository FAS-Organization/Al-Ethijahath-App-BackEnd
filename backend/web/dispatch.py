from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from customer.decisions import checked
from customer.ledger import staff_region
from customer.sharing import audit, value
from .models import EmployeeProfile, JobAssignment, JobEquipment, ServiceJob, ServiceUpdate


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def assign(request, pk):
    machine=get_object_or_404(JobEquipment.objects.select_for_update(),pk=pk)
    job=machine.job
    staff_region(request.user,"web.assign_technician",job.region_id)
    if job.status in ("closed","cancelled") or machine.status in ("completed","cancelled"):
        raise ValidationError("Choose an open service.")
    purpose=value(serializers.ChoiceField(choices=["inspection","repair"]),request.data,"purpose")
    tech=get_object_or_404(EmployeeProfile,pk=value(serializers.IntegerField(min_value=1),request.data,"technician_id"),
                           region_id=job.region_id,user__is_staff=True,user__is_active=True,user__groups__name="Technician")
    if tech.user_id==request.user.pk:
        raise PermissionDenied("A second staff member must dispatch this assignment.")
    previous=machine.assignments.select_for_update().filter(purpose=purpose,status__in=["accepted","pending"]).first()
    note=serializers.CharField(max_length=1500,allow_blank=True).run_validation(request.data.get("instructions",""))
    if previous:
        if previous.technician_id==tech.pk:
            return Response({"id":previous.pk,"status":previous.status})
        previous.end_reason=value(serializers.CharField(max_length=1000),request.data,"reason")
        previous.status,previous.ended_at="reassigned",timezone.now()
        previous.save(update_fields=["status","end_reason","ended_at","updated_at"])
        if machine.assigned_technician_id==previous.technician_id:
            machine.assigned_technician=None
            machine.save(update_fields=["assigned_technician","updated_at"])
    else:
        previous=machine.assignments.filter(purpose=purpose,status="declined",replacement_assignment=None).order_by("-pk").first()
    row=checked(JobAssignment(job_equipment=machine,technician=tech,purpose=purpose,assigned_by=request.user,
        created_by=request.user,previous_assignment=previous,instructions=note))
    audit(request.user,"assignment.dispatched",row,machine=machine.pk,technician=tech.pk)
    return Response({"id":row.pk,"status":row.status},status=201)


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def respond(request, pk):
    target=get_object_or_404(JobAssignment,pk=pk)
    machine=JobEquipment.objects.select_for_update().get(pk=target.job_equipment_id)
    row=JobAssignment.objects.select_for_update().get(pk=pk)
    staff_region(request.user,"web.respond_to_assignment",machine.job.region_id)
    if row.technician.user_id!=request.user.pk:
        raise PermissionDenied("You can respond only to your own assignment.")
    if row.status!="pending":
        raise ValidationError("This assignment has already been answered.")
    state=value(serializers.ChoiceField(choices=["accepted","declined"]),request.data,"status")
    if state=="declined":
        row.decline_reason=value(serializers.CharField(max_length=1500),request.data,"reason")
        row.ended_at=timezone.now()
    else:
        machine.assigned_technician=row.technician
        machine.save(update_fields=["assigned_technician","updated_at"])
        ServiceJob.objects.filter(pk=machine.job_id,status="enquiry").update(status="open")
    row.status,row.responded_at=state,timezone.now()
    checked(row)
    audit(request.user,"assignment."+state,row,machine=machine.pk)
    return Response({"id":row.pk,"status":row.status})


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def progress(request, pk):
    machine=get_object_or_404(JobEquipment.objects.select_for_update(),pk=pk)
    staff_region(request.user,"web.add_serviceupdate",machine.job.region_id)
    if not request.user.is_superuser and request.user.groups.filter(name="Technician").exists():
        if not machine.assigned_technician_id or machine.assigned_technician.user_id!=request.user.pk:
            raise PermissionDenied("You can update only assigned work.")
    if machine.job.status in ("cancelled","closed") or machine.status in ("completed","cancelled"):
        raise ValidationError("This service is closed.")
    allowed=["inspection","quotation","awaiting_approval","scheduled","awaiting_parts","in_progress","on_hold"]
    state=value(serializers.ChoiceField(choices=allowed),request.data,"status")
    message=value(serializers.CharField(max_length=2500),request.data,"message")
    visibility=value(serializers.ChoiceField(choices=["customer","internal"]),request.data,"visibility")
    machine.status=state
    if "estimated_completion" in request.data:
        machine.estimated_completion=value(serializers.DateTimeField(allow_null=True),request.data,"estimated_completion")
    if not machine.started_at:
        machine.started_at=timezone.now()
    machine.save(update_fields=["status","started_at","estimated_completion","updated_at"])
    ServiceJob.objects.filter(pk=machine.job_id,status__in=["enquiry","open"]).update(status="in_progress")
    update=ServiceUpdate.objects.create(job_equipment=machine,created_by=request.user,status=state,message=message,
                                        visibility=visibility,estimated_completion=machine.estimated_completion)
    audit(request.user,"service.progress",update,machine=machine.pk,status=state)
    return Response({"id":update.pk,"status":state},status=201)
