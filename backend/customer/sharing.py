import secrets
from datetime import timedelta
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as ModelError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework.response import Response

from web.models import AuditEvent, CustomerApprover, CustomerCompany, ServiceAccess, SharingChallenge, User
from .permissions import IsCustomer


def audit(actor, action, obj, **changes):
    AuditEvent.objects.create(actor=actor, action=action, model_label=obj._meta.label, record_id=str(obj.pk),
                              changes=changes, source="api")


def value(field, data, key):
    return field.run_validation(data.get(key, serializers.empty))


def digest(challenge, code):
    return salted_hmac("sharing.otp", f"{challenge.nonce}:{code}", algorithm="sha256").hexdigest()


@api_view(["POST"])
@permission_classes([IsCustomer])
def start(request):
    email = value(serializers.EmailField(), request.data, "email").lower()
    now = timezone.now()
    with transaction.atomic():
        company = CustomerCompany.objects.select_for_update().get(account_holder=request.user)
        target = None
        if "service_user_id" in request.data:
            pk = value(serializers.IntegerField(min_value=1), request.data, "service_user_id")
            target = get_object_or_404(company.delegates, pk=pk)
        last_hour = SharingChallenge.objects.filter(created_at__gte=now-timedelta(hours=1))
        if last_hour.filter(company=company).count() >= 12 or last_hour.filter(email=email).count() >= 6:
            raise Throttled(wait=3600)
        older = SharingChallenge.objects.filter(company=company, email=email, target=target)
        if older.filter(created_at__gt=now-timedelta(seconds=60)).exists():
            raise Throttled(wait=60)
        older.filter(consumed_at=None).update(consumed_at=now)
        row = SharingChallenge(company=company, email=email, target=target, created_by=request.user,
                               expires_at=now+timedelta(minutes=10))
        code = f"{secrets.randbelow(10000):04d}"
        row.digest = digest(row, code)
        row.save()
    try:
        sent = send_mail("Verify your Al Ethijahath service login",
            f"{company.name} wants to create or change a service-only login for this email. Give the account owner "
            f"code {code} only if you agree. It expires in 10 minutes.", settings.DEFAULT_FROM_EMAIL, [email])
    except (SMTPException, OSError):
        sent = 0
    if not sent:
        SharingChallenge.objects.filter(pk=row.pk).update(consumed_at=timezone.now())
        return Response({"message":"Email delivery failed. Request another code."}, status=503)
    return Response({"challenge":row.nonce, "expires_at":row.expires_at}, status=201)


@api_view(["POST"])
@permission_classes([IsCustomer])
@transaction.atomic
def verify(request, nonce):
    code = value(serializers.RegexField(r"^[0-9]{4}$", max_length=4), request.data, "code")
    row = get_object_or_404(SharingChallenge.objects.select_for_update(), company=request.user.customer_company,
        nonce=nonce, created_by=request.user, consumed_at=None, attempts__lt=5, expires_at__gt=timezone.now())
    if not constant_time_compare(row.digest, digest(row, code)):
        row.attempts += 1
        row.save(update_fields=["attempts"])
        return Response({"message":"Incorrect code."}, status=400)
    row.verified_at = timezone.now()
    row.save(update_fields=["verified_at"])
    return Response({"verified":True})


def public_grant(grant):
    return {"id":grant.pk, "email":grant.user.email, "is_enabled":grant.is_enabled}


def validated_password(request, user, name):
    pw = value(serializers.CharField(trim_whitespace=False, max_length=128), request.data, name)
    try:
        validate_password(pw, user)
    except ModelError as err:
        raise ValidationError({name:err.messages}) from err
    return pw


@api_view(["GET", "POST", "PATCH"])
@permission_classes([IsCustomer])
@transaction.atomic
def service_users(request, pk=None):
    company = request.user.customer_company
    grants = company.delegates.select_related("user")
    if request.method == "GET":
        if pk is not None:
            return Response(public_grant(get_object_or_404(grants, pk=pk)))
        return Response([public_grant(g) for g in grants.order_by("user__email")])
    if (request.method == "PATCH") != (pk is not None):
        raise ValidationError("Choose the service user to edit.")
    grant = get_object_or_404(grants.select_for_update(), pk=pk) if pk is not None else None
    if grant and "is_enabled" in request.data:
        if set(request.data) != {"is_enabled"}:
            raise ValidationError("Change access separately from credentials.")
        enabled = value(serializers.BooleanField(), request.data, "is_enabled")
        if enabled != grant.is_enabled:
            grant.token_version += 1
            grant.is_enabled = enabled
            grant.save(update_fields=["token_version", "is_enabled", "updated_at"])
            audit(request.user, "sharing.toggled", grant, enabled=enabled)
        return Response(public_grant(grant))
    email = value(serializers.EmailField(), request.data, "email").lower()
    nonce = value(serializers.UUIDField(), request.data, "challenge")
    proof = get_object_or_404(SharingChallenge.objects.select_for_update(), company=company, email=email,
        created_by=request.user, nonce=nonce, target=grant, consumed_at=None, verified_at__isnull=False,
        attempts__lt=5, expires_at__gt=timezone.now())
    user = grant.user if grant else User(is_active=True, account_status="active")
    if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
        raise ValidationError({"email":"This address already has a login."})
    user.email = email
    user.set_password(validated_password(request, user, "password"))
    user.password_changed_at = timezone.now()
    try:
        with transaction.atomic():
            user.save()
    except IntegrityError as err:
        raise ValidationError({"email":"This address already has a login."}) from err
    if grant:
        grant.token_version += 1
        grant.save(update_fields=["token_version", "updated_at"])
    else:
        grant = ServiceAccess.objects.create(company=company, user=user, created_by=request.user)
    proof.consumed_at = timezone.now()
    proof.save(update_fields=["consumed_at"])
    audit(request.user, "sharing.updated" if pk else "sharing.created", grant)
    return Response(public_grant(grant), status=200 if pk else 201)


class ApproverFields(serializers.ModelSerializer):
    class Meta:
        model = CustomerApprover
        fields = ["id","name","email","designation","is_active"]


@api_view(["GET", "POST", "PATCH"])
@permission_classes([IsCustomer])
@transaction.atomic
def approvers(request, pk=None):
    company = request.user.customer_company
    rows = company.authorized_approvers
    if request.method == "GET":
        return Response(ApproverFields(rows.all(), many=True).data)
    if (request.method == "PATCH") != (pk is not None):
        raise ValidationError("Choose a signer to edit.")
    obj = get_object_or_404(rows.select_for_update(), pk=pk) if pk else None
    form = ApproverFields(obj, data=request.data, partial=bool(pk))
    form.is_valid(raise_exception=True)
    try:
        saved = form.save(company=company, **({} if pk else {"created_by":request.user}))
    except IntegrityError as err:
        raise ValidationError("This email is already a signer for the company.") from err
    saved.deactivated_at = None if saved.is_active else (saved.deactivated_at or timezone.now())
    saved.save(update_fields=["deactivated_at"])
    audit(request.user, "approver.updated", saved)
    return Response(form.data, status=200 if pk else 201)
