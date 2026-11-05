from rest_framework.permissions import BasePermission


class IsCustomer(BasePermission):
    """Only an active customer account holder with an active company."""

    message = 'Customer account required.'

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        company = getattr(user, 'customer_company', None)
        return company is not None and company.is_active