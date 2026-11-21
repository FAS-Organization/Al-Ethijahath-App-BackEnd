from decimal import Decimal
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.core.exceptions import ValidationError as ModelError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from web.models import CreditNote, Invoice
from .permissions import IsCustomer


def staff_region(user, permission, region_id):
    if not user.is_active or not user.is_staff or not user.has_perm(permission):
        raise PermissionDenied()
    if not user.is_superuser:
        employee = getattr(user, "employee", None)
        if not employee or not (
            employee.global_scope or (region_id is not None and employee.region_id == region_id)
        ):
            raise PermissionDenied()


class CreditFields(serializers.ModelSerializer):
    class Meta:
        model = CreditNote
        fields = ("id", "number", "amount", "reason", "issued_at")
        read_only_fields = ("id", "issued_at")
        extra_kwargs = {"number": {"validators": []}}


def invoice_ledger(invoice):
    payments = [payment for payment in invoice.payments.all() if payment.status == "posted"]
    credits = list(invoice.credit_notes.all())
    zero = Decimal("0.00")
    paid = sum((payment.amount for payment in payments), zero)
    credited = sum((credit.amount for credit in credits), zero)
    balance = invoice.total - paid - credited if invoice.status != "void" else zero
    if invoice.status != "issued":
        status = "not_payable"
    elif balance <= zero:
        status = "paid"
    else:
        status = "partially_paid" if paid else "unpaid"
    today = timezone.localdate(timezone=ZoneInfo(invoice.region.timezone))
    return {
        "id": invoice.pk,
        "number": invoice.number,
        "currency": invoice.currency,
        "status": invoice.status,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "total": str(invoice.total),
        "amount_paid": str(paid),
        "amount_credited": str(credited),
        "balance_due": str(balance),
        "payment_status": status,
        "is_overdue": invoice.status == "issued" and balance > zero and invoice.due_date < today,
        "credit_notes": CreditFields(credits, many=True).data,
        "payments": [
            {
                "id": p.pk,
                "amount": str(p.amount),
                "paid_at": p.paid_at,
                "method": p.method,
                "transaction_reference": p.transaction_reference,
            }
            for p in payments
        ],
    }


@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def issue_credit_note(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_for_update(), pk=pk)
    staff_region(request.user, "web.add_creditnote", invoice.region_id)
    form = CreditFields(data=request.data)
    form.is_valid(raise_exception=True)
    values = form.validated_data
    existing = CreditNote.objects.filter(number=values["number"]).first()
    if existing:
        if (
            existing.invoice_id == invoice.pk
            and existing.amount == values["amount"]
            and existing.reason == values["reason"]
        ):
            return Response(CreditFields(existing).data)
        raise ValidationError({"number": "This credit note number is already in use."})
    note = CreditNote(invoice=invoice, created_by=request.user, **values)
    note._from_api = True
    try:
        note.save()
    except ModelError as exc:
        raise ValidationError(getattr(exc, "message_dict", exc.messages)) from exc
    except IntegrityError as exc:
        raise ValidationError({"number": "This credit note number is already in use."}) from exc
    return Response(CreditFields(note).data, status=201)


@api_view(["GET"])
@permission_classes([IsCustomer])
def ledger(request):
    items = (
        Invoice.objects.filter(company=request.user.customer_company)
        .exclude(status="draft")
        .select_related("region")
        .prefetch_related("payments", "credit_notes")
    )
    return Response(
        {
            "company_name": request.user.customer_company.name,
            "invoices": [invoice_ledger(i) for i in items],
        }
    )
