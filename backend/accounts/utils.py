from rest_framework.exceptions import Throttled,ValidationError
from rest_framework.views import exception_handler


def _first_message(detail):
    if isinstance(detail, dict):
        for key, value in detail.items():
            return f'{key}: {_first_message(value)}'
    if isinstance(detail, (list, tuple)) and detail:
        return _first_message(detail[0])
    return str(detail)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return response
    if isinstance(exc, Throttled):
        response.data = {
            'code': 'too_many_attempts',
            'message': 'Too many attempts. Please wait and try again.',
        }
    elif isinstance(exc, ValidationError):
        errors = response.data
        response.data = {
            'code': 'validation_error',
            'message': _first_message(errors),
            'errors': errors,
        }
    return response


def get_role(user):
    if hasattr(user, 'customer_company'):
        return 'customer'
    if user.is_superuser:
        return 'admin'
    groups = set(user.groups.values_list('name', flat=True))
    for group_name, role in (
        ('Manager', 'manager'),
        ('Admin', 'admin'),
        ('Technician', 'technician'),
        ('Sales Executive', 'sales_executive'),
    ):
        if group_name in groups:
            return role
    return 'staff' if hasattr(user, 'employee') else ''


def user_payload(user):
    return {
        'name': user.get_full_name() or user.email,
        'email': user.email,
        'role': get_role(user),
    }