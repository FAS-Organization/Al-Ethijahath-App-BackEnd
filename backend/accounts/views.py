from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes, throttle_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from web.models import CustomerCompany, CustomerSite, Region

from .utils import user_payload

User = get_user_model()

MESSAGES = {
    'invalid_credentials': 'Incorrect email or password. Please try again.',
    'pending_approval': 'Your account request is still waiting for approval from our team. You will receive an email once it is approved.',
    'rejected': 'Your account request was not approved. Please contact our support team.',
    'not_activated': 'Your account has not been activated yet. Please use the invitation email we sent you.',
    'inactive': 'This account is inactive. Please contact your administrator.',
}

STATUS_CODES = {
    'pending_approval': 'pending_approval',
    'rejected': 'rejected',
    'pending': 'not_activated',
    'inactive': 'inactive',
}

REGISTERED_MESSAGE = (
    'Your registration was submitted. Our team will review it and email you '
    'once your account is approved.'
)


class LoginThrottle(AnonRateThrottle):
    scope = 'login'


class RegisterThrottle(AnonRateThrottle):
    scope = 'register'


def fail(code, http_status):
    return Response({'code': code, 'message': MESSAGES[code]}, status=http_status)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def login_view(request):
    email = str(request.data.get('email') or '').strip().lower()
    password = str(request.data.get('password') or '')

    user = User.objects.filter(email__iexact=email).first()
    if user is None or not user.check_password(password):
        return fail('invalid_credentials', 401)

    # Password is correct, so it is safe to say why login is blocked.
    if user.account_status in STATUS_CODES:
        return fail(STATUS_CODES[user.account_status], 403)
    if not user.is_active:
        return fail('inactive', 403)
    company = getattr(user, 'customer_company', None)
    if company is not None and not company.is_active:
        return fail('inactive', 403)

    refresh = RefreshToken.for_user(user)
    return Response({
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': user_payload(user),
    })


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([RegisterThrottle])
def register_view(request):
    data = request.data
    full_name = str(data.get('fullName') or '').strip()
    company_name = str(data.get('companyName') or '').strip()
    email = str(data.get('email') or '').strip().lower()
    phone = str(data.get('phone') or '').strip()
    password = str(data.get('password') or '')

    errors = {}
    if not full_name:
        errors['fullName'] = 'Enter your full name.'
    if not company_name:
        errors['companyName'] = 'Enter your company name.'
    if not phone:
        errors['phone'] = 'Enter your phone number.'
    try:
        validate_email(email)
    except ValidationError:
        errors['email'] = 'Enter a valid email address.'
    try:
        validate_password(password)
    except ValidationError as exc:
        errors['password'] = ' '.join(exc.messages)
    region = None
    try:
        region = Region.objects.get(pk=int(data.get('regionId')), is_active=True)
    except (TypeError, ValueError, Region.DoesNotExist):
        errors['regionId'] = 'Select your region.'
    if errors:
        return Response(
            {
                'code': 'validation_error',
                'message': next(iter(errors.values())),
                'errors': errors,
            },
            status=400,
        )

    ok = Response({'ok': True, 'message': REGISTERED_MESSAGE}, status=201)

    # Same answer for an existing email, so emails cannot be probed.
    if User.objects.filter(email__iexact=email).exists():
        return ok

    first_name, _, last_name = full_name.partition(' ')
    try:
        with transaction.atomic():
            user = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                account_status='pending_approval',
                is_active=False,
            )
            user.set_password(password)
            user.save()
            company = CustomerCompany.objects.create(
                name=company_name,
                account_holder=user,
                billing_address='',
                contact_name=full_name,
                contact_email=email,
                contact_phone=phone,
                created_by=user,
            )
            CustomerSite.objects.create(
                company=company,
                region=region,
                name='Main site',
                address='',
                contact_name=full_name,
                contact_phone=phone,
                created_by=user,
            )
    except IntegrityError:
        pass
    return ok


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def logout_view(request):
    try:
        RefreshToken(request.data.get('refresh')).blacklist()
    except TokenError:
        pass
    return Response({'ok': True})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    return Response(user_payload(request.user))

@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def regions_view(request):
    regions = Region.objects.filter(is_active=True).order_by('name')
    return Response([{'id': r.id, 'name': r.name, 'code': r.code} for r in regions])