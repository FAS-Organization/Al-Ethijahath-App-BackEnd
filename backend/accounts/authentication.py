from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication


def check_portal_session(user, token):
    company = getattr(user, "customer_company", None)
    if not user.is_active or (company and not company.is_active):
        raise AuthenticationFailed("The account is inactive.")
    grant = getattr(user, "service_access", None)
    if grant and (not grant.enabled or token.get("access_version") != grant.token_version):
        raise AuthenticationFailed("Service access has changed. Sign in again.")
    return grant


class PortalAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            grant = check_portal_session(*result)
            if grant and request.resolver_match.url_name not in {"account-me", "service-requests"}:
                raise PermissionDenied("This login may only request service.")
        return result


class StaffSessionAuthentication(SessionAuthentication):
    """Allow staff to use portal operations from their CSRF-protected admin session."""
    def authenticate(self, request):
        user = getattr(request._request, "user", None)
        if user and user.is_active and user.is_staff:
            return super().authenticate(request)
        return None
