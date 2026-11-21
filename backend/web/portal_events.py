from django.db.models.signals import post_save
from django.dispatch import receiver

from customer.notices import notify
from .models import (
    CreditNote,
    GeneratedDocument,
    Invoice,
    JobCompletionCard,
    Payment,
    Quotation,
    RescheduleRequest,
)


@receiver(post_save, sender=Invoice)
def issued(sender, instance, raw=False, **kwargs):
    if not raw and instance.status == "issued":
        notify(
            instance.company,
            "invoice_issued",
            f"invoice:{instance.pk}:issued",
            "Invoice issued",
            f"{instance.number} was issued for {instance.currency} {instance.total}.",
            "/billing",
            invoice=instance,
        )


@receiver(post_save, sender=CreditNote)
def credited(sender, instance, raw=False, **kwargs):
    if not raw:
        i = instance.invoice
        notify(
            i.company,
            "credit_issued",
            f"credit:{instance.pk}",
            "Credit note issued",
            f"{instance.number} credits {i.currency} {instance.amount} against {i.number}.",
            "/billing",
            invoice=i,
        )


@receiver(post_save, sender=Payment)
def paid(sender, instance, raw=False, **kwargs):
    if not raw and instance.status == "posted":
        i = instance.invoice
        notify(
            i.company,
            "payment_recorded",
            f"payment:{instance.pk}",
            "Payment recorded",
            f"A payment of {i.currency} {instance.amount} was recorded against {i.number}.",
            "/billing",
            invoice=i,
        )


@receiver(post_save, sender=Quotation)
def quote_changed(sender, instance, raw=False, **kwargs):
    q = instance
    if not raw and q.status in ("sent", "approved", "rejected"):
        event = "quotation_sent" if q.status == "sent" else "quotation_decision"
        notify(
            q.job_equipment.job.company,
            event,
            f"quote:{q.pk}:{q.status}",
            f"Quotation {q.status}",
            f"{q.number} · Version {q.version} is {q.get_status_display().lower()}.",
            "/quotations",
            quotation=q,
        )


@receiver(post_save, sender=JobCompletionCard)
def completed(sender, instance, raw=False, **kwargs):
    if not raw and instance.status == "approved":
        m = instance.job_equipment
        notify(
            m.job.company,
            "service_completed",
            f"completion:{instance.pk}",
            "Service completion recorded",
            f"The completion card for service {m.reference} was approved.",
            "/services",
            job_equipment=m,
        )


@receiver(post_save, sender=RescheduleRequest)
def rescheduled(sender, instance, raw=False, **kwargs):
    if not raw and instance.status in ("accepted", "alternative", "rejected"):
        v = instance.appointment
        notify(
            v.job_equipment.job.company,
            "reschedule_decision",
            f"reschedule:{instance.pk}:{instance.status}:{instance.alternative_date}:{v.schedule_version}",
            "Visit request updated",
            f"Your date change for {v.job_equipment.reference} is {instance.get_status_display().lower()}.",
            "/maintenance",
            appointment=v,
        )


@receiver(post_save, sender=GeneratedDocument)
def report_published(sender, instance, raw=False, **kwargs):
    if (
        raw
        or instance.visibility != "customer"
        or instance.status != "ready"
        or not instance.published_at
    ):
        return
    report = instance.inspection_report or instance.completion_card
    if (
        not report
        or getattr(report, "purpose", "customer") != "customer"
        or report.status not in ("approved", "pending_approval")
    ):
        return
    approved = report.status == "approved"
    machine = report.job_equipment
    notify(
        machine.job.company,
        "report_approved" if approved else "report_approval",
        f"{report._meta.model_name}:{report.pk}:{report.status}",
        "Service report approved" if approved else "Inspection awaiting approval",
        f"Service {machine.reference}, version {report.version}: {report.get_status_display()}.",
        "/documents",
        job_equipment=machine,
        document=instance,
    )
