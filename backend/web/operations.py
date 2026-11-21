from decimal import Decimal

from django.core.exceptions import ValidationError as InvalidLine
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from customer.decisions import checked
from customer.ledger import staff_region
from customer.sharing import audit, value
from . import models as m
from .publication import publish


def next_number(region, kind):
    sequence, _ = m.DocumentNumberSequence.objects.get_or_create(
        region=region,
        document_type=kind,
        defaults={"prefix": f"{region.code}-{'INV' if kind=='invoice' else 'QT'}-"},
    )
    sequence = m.DocumentNumberSequence.objects.select_for_update().get(pk=sequence.pk)
    model = m.Invoice if kind == "invoice" else m.Quotation
    while True:
        number = f"{sequence.prefix}{sequence.next_number:06d}"
        sequence.next_number += 1
        if not model.objects.filter(region=region, number=number).exists():
            break
    sequence.save(update_fields=["next_number", "updated_at"])
    return number


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def issue(request, kind, pk):
    if kind not in ("invoice", "quotation"):
        raise serializers.ValidationError("Choose an invoice or quotation.")
    model = m.Invoice if kind == "invoice" else m.Quotation
    record = get_object_or_404(model.objects.select_for_update(), pk=pk)
    staff_region(request.user, "web.change_" + kind, record.region_id)
    if record.status != "draft":
        raise serializers.ValidationError("Only a draft can be issued.")
    company = record.company if kind == "invoice" else record.job_equipment.job.company
    lines = list(record.lines.select_for_update().order_by("pk"))
    if not lines:
        raise serializers.ValidationError("Add at least one line before issuing.")
    for line in lines:
        try:
            line.full_clean()
        except InvalidLine as exc:
            raise serializers.ValidationError(exc.messages) from exc
    record.subtotal = sum((line.line_total for line in lines), Decimal("0"))
    for field in ("discount", "tax_amount"):
        if field in request.data:
            amount = value(
                serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal("0")),
                request.data,
                field,
            )
            setattr(record, field, amount)
    record.total = record.subtotal - record.discount + record.tax_amount
    record.billing_snapshot = {
        "name": company.name,
        "address": company.billing_address,
        "tax_number": company.tax_number,
    }
    now = timezone.now()
    if kind == "quotation":
        record.expires_at = value(serializers.DateTimeField(), request.data, "expires_at")
        if record.expires_at <= now:
            raise serializers.ValidationError({"expires_at": "Choose a future expiry."})
        if record.previous_version_id:
            previous = m.Quotation.objects.select_for_update().get(pk=record.previous_version_id)
            if previous.status == "approved":
                raise serializers.ValidationError("An approved quotation cannot be replaced.")
            if previous.status == "sent":
                previous.status = "superseded"
                previous.save(update_fields=["status", "updated_at"])
            record.number = record.number or previous.number
        record.status, record.sent_at = "sent", now
    else:
        record.status, record.issued_at = "issued", now
    record.number = record.number or next_number(record.region, kind)
    checked(record)
    document = publish(request.user, kind, pk)
    audit(request.user, kind + ".issued", record, document=document.pk)
    return Response(
        {"id": record.pk, "number": record.number, "status": record.status, "document": document.pk}
    )


class PaymentForm(serializers.Serializer):
    idempotency_key = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal("0.01"))
    method = serializers.ChoiceField(choices=["cash", "bank_transfer", "qr", "other"])
    paid_at = serializers.DateTimeField()
    transaction_reference = serializers.CharField(
        max_length=150, required=False, allow_blank=True, default=""
    )
    corrects_id = serializers.IntegerField(min_value=1, required=False)


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def record_payment(request, pk):
    invoice = get_object_or_404(m.Invoice.objects.select_for_update(), pk=pk)
    staff_region(request.user, "web.add_payment", invoice.region_id)
    form = PaymentForm(data=request.data)
    form.is_valid(raise_exception=True)
    fields = form.validated_data
    old = m.Payment.objects.filter(idempotency_key=fields["idempotency_key"]).first()
    if old:
        if old.invoice_id != pk or any(getattr(old, key) != val for key, val in fields.items()):
            raise serializers.ValidationError("This payment reference has already been used.")
        return Response({"id": old.pk, "balance_due": str(invoice.balance_due)})
    if "corrects_id" in fields:
        get_object_or_404(invoice.payments, pk=fields["corrects_id"], status="void")
    row = checked(m.Payment(invoice=invoice, created_by=request.user, **fields))
    audit(request.user, "payment.posted", row, invoice=pk)
    return Response({"id": row.pk, "balance_due": str(invoice.balance_due)}, status=201)


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def submit_report(request, kind, pk):
    from customer.decisions import REPORTS, require_assigned_technician

    if kind not in REPORTS:
        raise serializers.ValidationError("Choose an inspection or completion card.")
    model, _ = REPORTS[kind]
    report = get_object_or_404(model.objects.select_for_update(), pk=pk)
    require_assigned_technician(request.user, report)
    if report.status != "draft" or getattr(report, "purpose", "customer") != "customer":
        raise serializers.ValidationError("Only a draft customer report can be submitted.")
    report.status, report.submitted_at = "pending_approval", timezone.now()
    checked(report)
    publish(request.user, kind, report.pk)
    audit(request.user, kind + ".submitted", report, version=report.version)
    return Response({"id": report.pk, "status": report.status})


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def void_entry(request, kind, pk):
    reason = value(serializers.CharField(max_length=1000), request.data, "reason")
    if kind == "payment":
        payment = get_object_or_404(m.Payment, pk=pk)
        invoice = m.Invoice.objects.select_for_update().get(pk=payment.invoice_id)
        staff_region(request.user, "web.change_payment", invoice.region_id)
        record = m.Payment.objects.select_for_update().get(pk=pk)
        if record.status != "posted":
            raise serializers.ValidationError("This payment has already been voided.")
        record.voided_at, record.voided_by = timezone.now(), request.user
    elif kind == "invoice":
        record = get_object_or_404(m.Invoice.objects.select_for_update(), pk=pk)
        invoice = record
        staff_region(request.user, "web.change_invoice", invoice.region_id)
        if record.status != "issued" or record.amount_credited or record.amount_paid:
            raise serializers.ValidationError(
                "Only an issued invoice with no posted payments or credits can be voided."
            )
    else:
        raise serializers.ValidationError("Choose an invoice or payment.")
    record.status, record.void_reason = "void", reason
    checked(record)
    audit(request.user, kind + ".voided", record, reason=reason)
    return Response({"id": pk, "status": record.status, "balance_due": str(invoice.balance_due)})
