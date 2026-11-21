from django.contrib import admin
from django.urls import include, path

from customer import decisions, schedule
from customer.ledger import issue_credit_note
from customer.rentals import review as rental_review

urlpatterns = [
    path("api/v1/staff/reports/<str:kind>/<int:pk>/tablet-approval/", decisions.tablet),
    path("api/v1/staff/reports/email-returns/<int:pk>/verify/", decisions.verify_return),
    path("api/v1/staff/quotations/<int:pk>/verify-reply/", decisions.verify_reply),
    path("api/v1/staff/reschedules/<int:pk>/review/", schedule.review),
    path('api/v1/staff/rentals/<int:pk>/review/', rental_review),
    path('api/v1/staff/invoices/<int:pk>/credit-notes/', issue_credit_note),
    path('admin/', admin.site.urls),
    path('api/v1/auth/', include('accounts.urls')),
    path('api/v1/customer/', include('customer.urls')),
]