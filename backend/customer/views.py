from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from django.db import transaction
from rest_framework.exceptions import ValidationError
from .permissions import IsCustomer
from .serializers import ProfileSerializer, SiteSerializer


def profile_payload(company):
    sites = list(company.sites.filter(is_active=True))
    data = dict(ProfileSerializer(company).data)
    data['has_site'] = bool(sites)
    data['profile_complete'] = bool(
        company.billing_address.strip() and any(s.address.strip() for s in sites)
    )
    return data


@api_view(['GET', 'PATCH'])
@permission_classes([IsCustomer])
def profile_view(request):
    company = request.user.customer_company
    if request.method == 'PATCH':
        serializer = ProfileSerializer(company, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            serializer.save()
            user = request.user
            if 'contact_name' in serializer.validated_data:
                user.first_name, _, user.last_name = company.contact_name.partition(' ')
            if 'contact_phone' in serializer.validated_data:
                user.phone = company.contact_phone
            user.save(update_fields=['first_name', 'last_name', 'phone'])
    return Response(profile_payload(company))


@api_view(['GET', 'POST'])
@permission_classes([IsCustomer])
def sites_view(request):
    company = request.user.customer_company
    if request.method == 'POST':
        serializer = SiteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        selected = serializer.validated_data.get('region')
        if not company.region_id or (selected and selected.pk != company.region_id):
            raise ValidationError({'region': 'Add sites in the registered account region.'})
        serializer.save(company=company, region=company.region, created_by=request.user)
        return Response(serializer.data, status=201)
    sites = company.sites.filter(is_active=True).select_related('region').order_by('name')
    return Response(SiteSerializer(sites, many=True).data)


@api_view(['PATCH'])
@permission_classes([IsCustomer])
def site_detail_view(request, pk):
    company = request.user.customer_company
    site = get_object_or_404(company.sites.select_related('region'), pk=pk)
    # Customers cannot move a site to another region; the Manager does that.
    data = {k: v for k, v in request.data.items() if k != 'region'}
    serializer = SiteSerializer(site, data=data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)