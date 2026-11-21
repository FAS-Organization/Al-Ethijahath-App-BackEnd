from django.urls import path
from .refresh import PortalRefreshView

from . import views

urlpatterns = [
    path('login/', views.login_view),
    path('register/', views.register_view),
    path('regions/', views.regions_view),
    path('logout/', views.logout_view),
    path('me/', views.me_view, name='account-me'),
    path('refresh/', PortalRefreshView.as_view()),
]