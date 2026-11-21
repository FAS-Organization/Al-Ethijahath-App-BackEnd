from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from web.models import RentalItem, RentalOrder, RentalOrderLine
from .ledger import staff_region
from .permissions import IsCustomer
from .sharing import audit


class Line(serializers.Serializer):
    id = serializers.SlugField()
    quantity = serializers.IntegerField(min_value=1)


def order_data(order):
    return {"id": f"RNT-{order.pk}", "order_id": order.pk, "status":order.status, "reason":order.reason,
            "ordered_on":order.created_at, "items":[{"id":r.item.code, "name":r.name_snapshot, "quantity":r.quantity}
                                                   for r in order.items.all()]}


@api_view(["GET","POST"])
@permission_classes([IsCustomer])
@transaction.atomic
def rentals(request):
    company = request.user.customer_company
    items = RentalItem.objects.filter(region_id=company.region_id, is_active=True, available__gt=0)
    if request.method == "GET":
        orders = company.rental_orders.prefetch_related("items__item").order_by("-pk")
        return Response({"items":[{"id":i.code, "name":i.name, "icon":i.icon, "description":i.description,
                                  "category":i.category, "available":i.available} for i in items.order_by("name")],
                         "orders":[order_data(o) for o in orders]})
    rows = Line(many=True, data=request.data.get("items"), allow_empty=False)
    rows.is_valid(raise_exception=True)
    quantities = {r["id"]:r["quantity"] for r in rows.validated_data}
    selected = list(items.filter(code__in=quantities).order_by("pk"))
    if (not company.region_id or len(quantities)!=len(rows.validated_data) or len(selected)!=len(quantities)
            or any(i.available < quantities[i.code] for i in selected)):
        raise ValidationError("Choose available quantities from your region.")
    order = RentalOrder.objects.create(company=company, region_id=company.region_id, created_by=request.user)
    for i in selected:
        RentalOrderLine.objects.create(order=order, item=i, name_snapshot=i.name, quantity=quantities[i.code], created_by=request.user)
    audit(request.user,"rental.requested",order)
    return Response(order_data(order),status=201)


@transaction.atomic
def record_outcome(user, pk, status, reason):
    order = get_object_or_404(RentalOrder.objects.select_for_update(), pk=pk)
    staff_region(user,"web.change_rentalorder",order.region_id)
    if status not in ("confirmed","rejected") or order.status != "processing":
        raise ValidationError("Choose a pending order and a valid outcome.")
    if status=="rejected" and not reason.strip():
        raise ValidationError("Enter a decline reason.")
    order.status, order.reviewed_by, order.reviewed_at, order.reason = status, user, timezone.now(), reason.strip()
    order.save(update_fields=["status","reviewed_by","reviewed_at","reason","updated_at"])
    audit(user,"rental."+status,order)
    return order


@api_view(["POST"])
@permission_classes([IsAdminUser])
def review(request,pk):
    state = serializers.ChoiceField(choices=["confirmed","rejected"]).run_validation(request.data.get("status"))
    reason = serializers.CharField(max_length=1500,allow_blank=True).run_validation(request.data.get("reason",""))
    return Response(order_data(record_outcome(request.user,pk,state,reason)))
