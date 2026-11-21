from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.views import TokenRefreshView
from .authentication import check_portal_session


class RefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        token = self.token_class(attrs["refresh"])
        user = get_user_model().objects.filter(pk=token.get("user_id")).first()
        if not user:
            raise AuthenticationFailed("Login unavailable.")
        check_portal_session(user, token)
        return super().validate(attrs)


class PortalRefreshView(TokenRefreshView):
    serializer_class = RefreshSerializer
