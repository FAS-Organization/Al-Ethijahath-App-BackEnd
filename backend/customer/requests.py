from zoneinfo import ZoneInfo

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from web.models import Equipment, JobEquipment, ServiceAccess, ServiceJob
from .sharing import audit


class ServiceRequest(serializers.Serializer):
    site_id = serializers.IntegerField(min_value=1)
    equipment_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), default=list)
    description = serializers.CharField(min_length=10, max_length=1000)
    preferred_date = serializers.DateField(required=False, allow_null=True)
    photo = serializers.ImageField(required=False, allow_null=True)

    def validate_photo(self, file):
        if file and (
            file.size > 5 * 1024 * 1024 or file.image.format not in {"JPEG", "PNG", "WEBP"}
        ):
            raise ValidationError("Choose a JPG, PNG or WebP of 5 MB or less.")
        return file


def suffix(index):
    result = ""
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


@api_view(["GET", "POST"])
@transaction.atomic
def service_requests(request):
    grant = getattr(request.user, "service_access", None)
    company = getattr(request.user, "customer_company", None)
    if grant:
        grant = ServiceAccess.objects.select_for_update().get(pk=grant.pk)
        if (
            not grant.enabled
            or not request.auth
            or request.auth.get("access_version") != grant.token_version
        ):
            raise PermissionDenied("Service access has changed.")
        company = grant.company
    if not company or not company.is_active:
        raise PermissionDenied()
    sites = company.sites.filter(is_active=True, region__is_active=True)
    equipment = Equipment.objects.filter(company=company, current_site__in=sites, is_retired=False)
    if request.method == "GET":
        return Response(
            {
                "sites": [
                    {"id": s.pk, "name": s.name, "region": {"name": s.region.name}}
                    for s in sites.select_related("region").order_by("name")
                ],
                "equipment": [
                    {
                        "id": e.pk,
                        "site_id": e.current_site_id,
                        "label": str(e),
                        "asset": e.customer_asset_number,
                    }
                    for e in equipment.order_by("equipment_type", "pk")
                ],
            }
        )
    form = ServiceRequest(data=request.data)
    # Browsers submit repeated equipment_ids entries for an optional photo.
    if hasattr(request.data, "getlist"):
        payload = {k: request.data[k] for k in request.data}
        payload["equipment_ids"] = request.data.getlist("equipment_ids") or request.data.getlist(
            "equipment_ids[]"
        )
        form = ServiceRequest(data=payload)
    form.is_valid(raise_exception=True)
    v = form.validated_data
    site = get_object_or_404(sites, pk=v["site_id"])
    at_site = equipment.filter(current_site=site)
    ids = v["equipment_ids"]
    selected = at_site.select_for_update().in_bulk(ids)
    if len(selected) != len(ids) or (not ids and at_site.exists()):
        raise ValidationError({"equipment_ids": "Select equipment from this site."})
    date = v.get("preferred_date")
    if date and date < timezone.localdate(timezone=ZoneInfo(site.region.timezone)):
        raise ValidationError({"preferred_date": "Choose today or a later date."})
    job = ServiceJob.objects.create(
        company=company,
        site=site,
        region=site.region,
        job_type="service",
        description=v["description"],
        site_address_snapshot=site.address,
        requested_by=request.user,
        created_by=request.user,
        requested_date=date,
    )
    for i, pk in enumerate(ids, 1):
        e = selected[pk]
        JobEquipment.objects.create(
            job=job,
            equipment=e,
            suffix=suffix(i),
            reported_issue=job.description,
            created_by=request.user,
            equipment_snapshot={
                k: getattr(e, k)
                for k in (
                    "brand",
                    "equipment_type",
                    "serial_number",
                    "model_name",
                    "customer_asset_number",
                )
            },
        )
    if v.get("photo"):
        job.request_photo = v["photo"]
        job.save(update_fields=["request_photo"])
    audit(request.user, "service.requested", job, company_id=company.pk, site_id=site.pk)
    return Response({"id": job.pk, "status": job.status}, status=201)
