from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from web.models import ContactPreferences, Notification, NotificationDelivery
from .permissions import IsCustomer


def category(event):
    if event.startswith(("payment_","invoice_")) or event=="credit_issued":
        return "billing"
    if event.startswith("quotation_"):
        return "quotations"
    if event.startswith("report_") or event=="amc_summary":
        return "reports"
    return "schedule" if event in ("upcoming_service","reschedule_decision") else "services"


def can_email(c,event):
    if event in ("quotation_sent","report_approval"):
        return True
    prefs=getattr(c,"preferences",None) or ContactPreferences()
    topic={"billing":"payments","quotations":"quotations"}.get(category(event),"visits")
    return prefs.email and getattr(prefs,topic)


def notify(c,event,key,title,message,destination,**related):
    if not c.is_active or not c.account_holder.is_active:
        return None
    note,_=Notification.objects.get_or_create(recipient=c.account_holder,event_key=key,defaults={"event_type":event,
        "title":title,"message":message,"destination_path":destination,**related})
    if can_email(c,event):
        NotificationDelivery.objects.get_or_create(notification=note,channel="email",defaults={"destination":c.account_holder.email})
    return note


def notices_for(c):
    result=[]
    for n in c.account_holder.notifications.select_related("invoice__job__site","quotation__job_equipment__job__site",
            "appointment__job_equipment__job__site","job_equipment__job__site").order_by("-created_at","-pk"):
        ref,site,action="","",False
        machine=n.job_equipment
        if n.invoice_id:
            if n.invoice.company_id!=c.pk or n.invoice.status=="draft":
                continue
            ref,site=n.invoice.number,n.invoice.job.site.name
            action=n.event_type=="payment_overdue" and n.invoice.is_overdue
        if n.quotation_id:
            machine=n.quotation.job_equipment
            if n.quotation.status=="draft":
                continue
            ref=n.quotation.number
            action=n.quotation.status=="sent" and not n.quotation.is_expired
        if n.appointment_id:
            machine=n.appointment.job_equipment
        if machine:
            if machine.job.company_id!=c.pk:
                continue
            ref,site=ref or machine.reference,machine.job.site.name
        result.append({"id":n.pk,"title":n.title,"message":n.message,"event_type":n.event_type,"category":category(n.event_type),
            "read_at":n.read_at,"created_at":n.created_at,"site":site,"reference":ref,"priority":"high" if action else "normal",
            "requires_action":action,"destination_path":n.destination_path,"action_label":"View details"})
    return result


@api_view(["GET","POST"])
@permission_classes([IsCustomer])
def notifications(request,pk=None):
    c=request.user.customer_company
    if request.method=="POST":
        read=serializers.BooleanField().run_validation(request.data.get("read",True))
        rows=request.user.notifications
        if pk:
            n=get_object_or_404(rows,pk=pk)
            if bool(n.read_at)!=read:
                n.read_at=timezone.now() if read else None
                n.save(update_fields=["read_at","updated_at"])
        elif read:
            rows.filter(read_at=None).update(read_at=timezone.now())
        else:
            raise serializers.ValidationError("Choose an individual notification to mark unread.")
    items=notices_for(c)
    return Response({"notifications":items,"unread_count":sum(n["read_at"] is None for n in items)})
