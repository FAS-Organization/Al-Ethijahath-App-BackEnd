from rest_framework import serializers
from web.models import CustomerCompany, CustomerSite, Region


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerCompany
        fields = [
            'name', 'registration_number', 'tax_number', 'billing_address',
            'contact_name', 'contact_email', 'contact_phone',
        ]
        read_only_fields = ['name', 'contact_email']


class SiteSerializer(serializers.ModelSerializer):
    region = serializers.PrimaryKeyRelatedField(
        queryset=Region.objects.filter(is_active=True)
    )
    region_name = serializers.CharField(source='region.name', read_only=True)

    class Meta:
        model = CustomerSite
        fields = [
            'id', 'name', 'address', 'region', 'region_name',
            'contact_name', 'contact_phone',
        ]
        extra_kwargs = {'address': {'allow_blank': False, 'required': True}}