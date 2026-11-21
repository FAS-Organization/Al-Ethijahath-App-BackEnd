from django.urls import path

from . import ledger, views

urlpatterns = [
    path('billing/ledger/', ledger.ledger),
    path('profile/', views.profile_view),
    path('sites/', views.sites_view),
    path('sites/<int:pk>/', views.site_detail_view),
]