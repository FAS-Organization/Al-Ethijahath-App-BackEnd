from django.urls import path

from . import account, ledger, rentals, requests, sharing, views

urlpatterns = [
    path("account/", account.account),
    path("account/preferences/", account.preferences),
    path("account/password/", account.password),
    path("approvers/", sharing.approvers),
    path("approvers/<int:pk>/", sharing.approvers),
    path("service-access/", sharing.service_users),
    path("service-access/<int:pk>/", sharing.service_users),
    path("service-access/challenges/", sharing.start),
    path("service-access/challenges/<uuid:nonce>/verify/", sharing.verify),
    path("service-requests/", requests.service_requests, name="service-requests"),
    path("rentals/", rentals.rentals),
    path('billing/ledger/', ledger.ledger),
    path('profile/', views.profile_view),
    path('sites/', views.sites_view),
    path('sites/<int:pk>/', views.site_detail_view),
]