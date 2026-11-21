from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from web.models import ContactPreferences, User
from .permissions import IsCustomer
from .sharing import audit, validated_password, value
from .serializers import SiteSerializer


class Channels(serializers.Serializer):
    email = serializers.BooleanField()
    whatsapp = serializers.BooleanField()
    sms = serializers.BooleanField()


class Topics(serializers.Serializer):
    quotations = serializers.BooleanField()
    visits = serializers.BooleanField()
    payments = serializers.BooleanField()


def prefs(p):
    return {"channels":Channels(p).data,"topics":Topics(p).data}


@api_view(["GET"])
@permission_classes([IsCustomer])
def account(request):
    c = request.user.customer_company
    sites = [{**SiteSerializer(s).data,"equipment_count":s.equipment.filter(company=c,is_retired=False).count()}
             for s in c.sites.filter(is_active=True).select_related("region")]
    return Response({"is_demo":False,"account":{"id":c.pk,"customer_number":f"CUST-{c.pk:04d}","member_since":c.created_at.date(),
        "status":request.user.account_status, "company":{"name":c.name,"billing_address":c.billing_address,"tax_number":c.tax_number},
        "region":({"id":c.region_id,"name":c.region.name,"currency":c.region.currency} if c.region_id else None),
        "contact":{"email":request.user.email,"phone":c.contact_phone,"full_name":c.contact_name}, "sites":sites,
        "password_changed_at":request.user.password_changed_at, "preferences":prefs(getattr(c,"preferences",None) or ContactPreferences()),
        "summary":{"equipment_count":c.equipment.filter(is_retired=False).count(),
                   "open_jobs":c.jobs.exclude(status__in=["closed","completed","cancelled"]).count(),
                   "open_invoices":sum(i.balance_due>0 for i in c.invoices.filter(status="issued"))}}})


@api_view(["PUT"])
@permission_classes([IsCustomer])
def preferences(request):
    channels = Channels(data=request.data.get("channels"))
    topics = Topics(data=request.data.get("topics"))
    channels.is_valid(raise_exception=True)
    topics.is_valid(raise_exception=True)
    defaults = {**channels.validated_data,**topics.validated_data}
    row,_ = ContactPreferences.objects.update_or_create(company=request.user.customer_company, defaults=defaults,
        create_defaults={**defaults,"created_by":request.user})
    return Response(prefs(row))


@api_view(["POST"])
@permission_classes([IsCustomer])
@transaction.atomic
def password(request):
    u=User.objects.select_for_update().get(pk=request.user.pk)
    current=value(serializers.CharField(max_length=128,trim_whitespace=False),request.data,"current_password")
    pw=validated_password(request,u,"new_password")
    if not u.check_password(current) or pw==current:
        raise ValidationError("Confirm the current password and enter a different new password.")
    u.set_password(pw)
    u.password_changed_at=timezone.now()
    u.save(update_fields=["password","password_changed_at"])
    audit(u,"account.password_changed",u)
    return Response({"password_changed_at":u.password_changed_at})
