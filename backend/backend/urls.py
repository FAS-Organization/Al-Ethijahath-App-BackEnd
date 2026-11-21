from django.contrib import admin
from django.urls import include, path

from customer.ledger import issue_credit_note

urlpatterns = [
    path('api/v1/staff/invoices/<int:pk>/credit-notes/', issue_credit_note),
    path('admin/', admin.site.urls),
    path('api/v1/auth/', include('accounts.urls')),
    path('api/v1/customer/', include('customer.urls')),
]