from django.contrib import admin
from django.urls import include, path

from web import operations, staff_forms
from web.publication import publish_view
from customer import decisions, schedule
from customer.ledger import issue_credit_note
from customer.rentals import review as rental_review

urlpatterns = [
    path("admin/portal/<slug:task>/<int:pk>/", admin.site.admin_view(staff_forms.workflow), name="portal-workflow"),
    path("api/v1/staff/reports/<str:kind>/<int:pk>/submit/", operations.submit_report),
    path("api/v1/staff/billing/<str:kind>/<int:pk>/void/", operations.void_entry),
    path("api/v1/staff/billing/<str:kind>/<int:pk>/issue/", operations.issue),
    path("api/v1/staff/invoices/<int:pk>/payments/", operations.record_payment),
    path("api/v1/staff/documents/<str:kind>/<int:pk>/publish/", publish_view),
    path("api/v1/staff/reports/<str:kind>/<int:pk>/technician-signature/", decisions.technician_signature),
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