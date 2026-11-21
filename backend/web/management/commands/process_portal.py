from collections import defaultdict
from datetime import datetime, time, timedelta
from smtplib import SMTPException
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from customer.documents import pdfs
from customer.notices import can_email, notify
from customer.pdf import render_pdf
from web.models import (AMCContract, AMCMonthlySummary, Invoice, NotificationDelivery, QuoteEmailRequest, Quotation,
                        ReminderOccurrence, ReminderPolicy, ServiceAppointment)


def eligible(note):
    if note.quotation_id and note.event_type in ("quotation_reminder","quotation_sent"):
        return note.quotation.status=="sent" and not note.quotation.is_expired
    if note.invoice_id and note.event_type=="payment_overdue":
        return note.invoice.is_overdue
    if note.appointment_id and note.event_type=="upcoming_service":
        return note.appointment.scheduled_start>timezone.now() and note.appointment.status in ("planned","confirmed")
    return True


def due_period(policy,start,today):
    delta=(today-start).days-policy.days_offset
    if delta<0:
        return None
    interval=policy.repeat_interval_days
    return start+timedelta(days=policy.days_offset+(delta//interval*interval if interval else 0))


def reminders():
    for policy in ReminderPolicy.objects.filter(is_enabled=True,region__is_active=True).select_related("region"):
        tz=ZoneInfo(policy.region.timezone)
        today=timezone.localdate(timezone=tz)
        if policy.reminder_type=="payment_overdue":
            rows=Invoice.objects.filter(region=policy.region,status="issued",due_date__lt=today)
        elif policy.reminder_type=="upcoming_service":
            rows=ServiceAppointment.objects.filter(job_equipment__job__region=policy.region,status__in=["planned","confirmed"],scheduled_start__gt=timezone.now())
        else:
            rows=Quotation.objects.filter(region=policy.region,status="sent",expires_at__gt=timezone.now())
        for obj in rows.iterator():
            if isinstance(obj,Invoice):
                if obj.balance_due<=0:
                    continue
                c,anchor,kind,event=obj.company,obj.due_date,"invoice","payment_overdue"
                text=f"{obj.number} has {obj.currency} {obj.balance_due} outstanding."
                title,dest="Overdue invoice reminder","/billing"
            elif isinstance(obj,Quotation):
                c,anchor,kind,event=obj.job_equipment.job.company,timezone.localdate(obj.sent_at,timezone=tz),"quotation","quotation_reminder"
                title,dest,text="Quotation awaiting a decision","/quotations",f"Review quotation {obj.number} before its expiry."
            else:
                c,anchor,kind,event=obj.job_equipment.job.company,timezone.localdate(obj.scheduled_start,timezone=tz),"appointment","upcoming_service"
                title,dest,text="Upcoming service visit","/maintenance",f"Visit for {obj.job_equipment.reference} is scheduled for {anchor}."
            period=due_period(policy,anchor,today)
            if kind=="quotation" and today==timezone.localdate(obj.expires_at,timezone=tz)-timedelta(days=1):
                period=today
            if period is None:
                continue
            version=getattr(obj,"schedule_version",None)
            key=f"reminder:{policy.pk}:{kind}:{obj.pk}:{version}:{period}"
            with transaction.atomic():
                occ,_=ReminderOccurrence.objects.get_or_create(occurrence_key=key,defaults={"policy":policy,kind:obj,
                    "appointment_version":version,"scheduled_at":datetime.combine(period,time.min,tzinfo=tz)})
                occ=ReminderOccurrence.objects.select_for_update().get(pk=occ.pk)
                if occ.status=="pending":
                    note=notify(c,event,key,title,text,dest,**{kind:obj})
                    occ.notification,occ.status,occ.processed_at=note,"processed" if note else "cancelled",timezone.now()
                    occ.save(update_fields=["notification","status","processed_at"])


def monthly():
    for contract in AMCContract.objects.exclude(status="draft").select_related("company__region"):
        c=contract.company
        tz=ZoneInfo(c.region.timezone if c.region_id else "Asia/Riyadh")
        end=timezone.localdate(timezone=tz).replace(day=1)
        start=(end-timedelta(days=1)).replace(day=1)
        if contract.start_date>=end or contract.end_date<start:
            continue
        with transaction.atomic():
            snapshot,_=AMCMonthlySummary.objects.get_or_create(contract=contract,month_start=start)
            snapshot=AMCMonthlySummary.objects.select_for_update().get(pk=snapshot.pk)
            if snapshot.published_at:
                continue
            visits=[]
            for v in ServiceAppointment.objects.filter(job_equipment__amc_coverage__contract=contract,job_equipment__job__company=c,
                    status="completed",purpose="maintenance").select_related("job_equipment__equipment","job_equipment__job__site"):
                when=timezone.localdate(v.actual_end or v.scheduled_end,timezone=tz)
                if start<=when<end:
                    m=v.job_equipment
                    visits.append({"id":v.pk,"date":when.isoformat(),"reference":m.reference,"site":m.job.site.name,
                        "equipment":{"id":m.equipment_id,"name":str(m.equipment),"asset":m.equipment.customer_asset_number}})
            snapshot.visits=visits
            snapshot.summary=f"{len(visits)} completed maintenance visits for {contract.contract_number} in {start:%B %Y}."
            table=[["Date","Site","Equipment","Reference"]]+[[v["date"],v["site"],v["equipment"]["name"],v["reference"]] for v in visits]
            snapshot.file.save("monthly-summary.pdf",ContentFile(render_pdf("AMC monthly summary",[c.name,snapshot.summary],table)),save=False)
            snapshot.published_at=timezone.now()
            snapshot.save()
            notify(c,"amc_summary",f"amc-summary:{snapshot.pk}","Your monthly AMC summary",snapshot.summary,"/maintenance")


def send_quote(pk):
    with transaction.atomic():
        row=QuoteEmailRequest.objects.select_for_update(of=("self",)).select_related("document__quotation__job_equipment__job__company__account_holder").get(pk=pk)
        if row.sent_at or row.cancelled_at or row.attempts>=5 or (row.last_attempt_at and row.last_attempt_at>timezone.now()-timedelta(minutes=5)):
            return
        q=row.document.quotation
        if not q:
            return
        c=q.job_equipment.job.company
        if not (c.is_active and c.account_holder.is_active and c.account_holder.email.lower()==row.recipient.lower()
                and q.status=="sent" and not q.is_expired and pdfs(c).filter(pk=row.document_id).exists()):
            row.cancelled_at=timezone.now()
            row.save(update_fields=["cancelled_at"])
            return
        row.attempts+=1
        row.last_attempt_at=timezone.now()
        try:
            message=EmailMessage(f"Quotation {q.number} · Version {q.version}",
                "Reply from your registered address with your name, role and acceptance. Copy your purchasing contact or authorized approver. "
                "Attach a purchase order if one was issued, or state that your company does not issue one. Sales will verify your reply.",
                settings.DEFAULT_FROM_EMAIL,[row.recipient],headers={"Message-ID":f"<quote-{row.pk}@alethijahath.com>"})
            with row.document.file.open("rb") as handle:
                message.attach(f"{row.document.document_number}.pdf",handle.read(),"application/pdf")
            if message.send():
                row.sent_at=timezone.now()
        except (SMTPException,OSError):
            pass
        row.save(update_fields=["attempts","last_attempt_at","sent_at"])


def send_notice(pk):
    with transaction.atomic():
        row=NotificationDelivery.objects.select_for_update().get(pk=pk)
        if row.status not in ("pending","failed") or row.attempt_count>=5 or (row.next_retry_at and row.next_retry_at>timezone.now()):
            return
        n=row.notification
        c=getattr(n.recipient,"customer_company",None)
        if not c or not c.is_active or not n.recipient.is_active or row.destination!=n.recipient.email or not eligible(n) or not can_email(c,n.event_type):
            row.status="cancelled"
        else:
            row.attempt_count+=1
            row.last_attempt_at=timezone.now()
            try:
                count=EmailMessage(n.title,n.message,settings.DEFAULT_FROM_EMAIL,[row.destination],
                    headers={"Message-ID":f"<portal-{row.pk}@alethijahath.com>"}).send()
            except (SMTPException,OSError):
                count=0
            row.status="sent" if count else "failed"
            row.sent_at=timezone.now() if count else None
            row.next_retry_at=None if count else timezone.now()+timedelta(minutes=5*row.attempt_count)
        row.save()


class Command(BaseCommand):
    help="Generate per-visit, invoice and quotation reminders, publish monthly AMC summaries and deliver queued email."

    def handle(self,*args,**options):
        reminders()
        monthly()
        cutoff=timezone.now()-timedelta(minutes=5)
        for pk in QuoteEmailRequest.objects.filter(sent_at=None,cancelled_at=None,attempts__lt=5).filter(
                Q(last_attempt_at=None)|Q(last_attempt_at__lte=cutoff)).values_list("pk",flat=True).order_by("pk")[:200]:
            send_quote(pk)
        for pk in NotificationDelivery.objects.filter(channel="email",status__in=["pending","failed"],attempt_count__lt=5).filter(
                Q(next_retry_at=None)|Q(next_retry_at__lte=timezone.now())).values_list("pk",flat=True).order_by("pk")[:200]:
            send_notice(pk)
