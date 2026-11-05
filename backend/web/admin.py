from django.contrib import admin
from django import forms
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

from .models import CustomerCompany, User

from . import models

# Custom user forms: our User uses email instead of username.
class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = models.User
        fields = ("email",)


class CustomUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = models.User
        fields = "__all__"


@admin.register(models.User)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = models.User

    ordering = ("email",)
    search_fields = ("email", "first_name", "last_name")
    list_display = (
        "email",
        "first_name",
        "last_name",
        "account_status",
        "is_active",
        "is_staff",
    )
    list_filter = ("account_status", "is_active", "is_staff")

    fieldsets = (
        (None, {
            "fields": ("email", "password"),
        }),
        ("Personal information", {
            "fields": (
                "first_name",
                "last_name",
                "phone",
                "preferred_language",
                "timezone",
            ),
        }),
        ("Account and permissions", {
            "fields": (
                "account_status",
                "is_active",
                "is_staff",
                "is_superuser",
                "groups",
                "user_permissions",
            ),
        }),
        ("Dates", {
            "fields": ("last_login", "date_joined"),
        }),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "email",
                "first_name",
                "last_name",
                "password1",
                "password2",
                "account_status",
                "is_active",
                "is_staff",
            ),
        }),
    )


# Basic registration for initial local development.
admin.site.register([
    models.Region,
    models.EmployeeProfile,
    models.CustomerCompany,
    models.CustomerSite,
    models.DocumentNumberSequence,
    models.Equipment,
    models.AMCContract,
    models.AMCEquipmentCoverage,
    models.ServiceJob,
    models.JobEquipment,
    models.ServiceAppointment,
    models.RescheduleRequest,
    models.ServiceUpdate,
    models.InspectionReport,
    models.InspectionFinding,
    models.JobCompletionCard,
    models.CompletionWorkItem,
    models.DocumentApproval,
    models.Invoice,
    models.InvoiceLine,
    models.Payment,
    models.DocumentTemplate,
    models.GeneratedDocument,
    models.ServiceAttachment,
    models.Notification,
    models.NotificationDelivery,
    models.ReminderPolicy,
    models.ReminderOccurrence,
    models.CustomerApprover,
    models.JobAssignment,
    models.InternalPurchaseReport,
    models.InternalPurchaseReportLine,
    models.Quotation,
    models.QuotationLine,
    models.EmailApprovalSubmission,
])

# Audit records should be view-only in admin.
@admin.register(models.AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "actor",
        "action",
        "model_label",
        "record_id",
        "source",
    )
    list_filter = ("source", "model_label")
    search_fields = ("action", "record_id")
    readonly_fields = (
        "actor",
        "action",
        "model_label",
        "record_id",
        "occurred_at",
        "changes",
        "reason",
        "source",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

class CustomerCompanyForm(forms.ModelForm):
    account_status = forms.ChoiceField(
        choices=User.Status.choices,
        label='Account status',
        help_text='Set to Active and save to approve this customer. They will be emailed.',
    )

    class Meta:
        model = CustomerCompany
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['account_status'].initial = (
                self.instance.account_holder.account_status
            )
        else:
            self.fields['account_status'].initial = User.Status.PENDING_APPROVAL


admin.site.unregister(CustomerCompany)


@admin.register(CustomerCompany)
class CustomerCompanyAdmin(admin.ModelAdmin):
    form = CustomerCompanyForm
    list_display = ('name', 'contact_name', 'contact_email', 'contact_phone', 'regions', 'status', 'created_at')
    list_filter = ('account_holder__account_status', 'sites__region', 'is_active')
    search_fields = ('name', 'contact_name', 'contact_email', 'account_holder__email')
    ordering = ('-created_at',)
    list_select_related = ('account_holder',)

    @admin.display(description='Account status', ordering='account_holder__account_status')
    def status(self, obj):
        return obj.account_holder.get_account_status_display()
        
    @admin.display(description='Region')
    def regions(self, obj):
        return ', '.join(sorted({s.region.name for s in obj.sites.all()})) or '-'

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('sites__region')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        new_status = form.cleaned_data['account_status']
        user = obj.account_holder
        if user.account_status == new_status:
            return

        user.account_status = new_status
        user.is_active = new_status == User.Status.ACTIVE
        user.save(update_fields=['account_status', 'is_active'])

        if new_status == User.Status.ACTIVE:
            subject = 'Your Al Ethijahath account is approved'
            body = (
                f'Hello {user.get_full_name() or user.email},\n\n'
                'Your company account has been approved. You can now sign in '
                'to the Al Ethijahath client portal with your email and password.\n\n'
                'Al Ethijahath'
            )
        elif new_status == User.Status.REJECTED:
            subject = 'Your Al Ethijahath account request'
            body = (
                f'Hello {user.get_full_name() or user.email},\n\n'
                'We could not approve your account request. Please contact '
                'our support team for details.\n\nAl Ethijahath'
            )
        else:
            return
        transaction.on_commit(
            lambda: send_mail(
                subject, body, settings.DEFAULT_FROM_EMAIL, [user.email],
                fail_silently=True,
            )
        )