from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

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