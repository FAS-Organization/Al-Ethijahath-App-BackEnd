from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html_join

from . import models as m


# Paths lead from an operational record to its single regional account.
REGION_PATHS = {
    m.Region: ("pk",), m.EmployeeProfile: ("region",), m.CustomerCompany: ("region",),
    m.CustomerSite: ("region",), m.Equipment: ("company__region",), m.ServiceJob: ("region",),
    m.AMCContract: ("company__region",), m.AMCEquipmentCoverage: ("contract__company__region",),
    m.JobEquipment: ("job__region",), m.Invoice: ("region",), m.InvoiceLine: ("invoice__region",),
    m.Payment: ("invoice__region",), m.CreditNote: ("invoice__region",), m.Quotation: ("region",),
    m.QuotationLine: ("quotation__region",), m.ReminderPolicy: ("region",), m.DocumentNumberSequence: ("region",),
    m.CustomerApprover: ("company__region",), m.RentalItem: ("region",), m.RentalOrder: ("region",),
    m.RentalOrderLine: ("order__region",), m.RescheduleRequest: ("appointment__job_equipment__job__region",),
    m.ReminderOccurrence: ("policy__region",), m.InspectionFinding: ("report__job_equipment__job__region",),
    m.CompletionWorkItem: ("card__job_equipment__job__region",),
    m.InternalPurchaseReportLine: ("report__job_equipment__job__region",),
    m.AMCMonthlySummary: ("contract__company__region",),
    m.Notification: ("recipient__customer_company__region","recipient__employee__region"),
    m.NotificationDelivery: ("notification__recipient__customer_company__region","notification__recipient__employee__region"),
    m.User: ("customer_company__region","employee__region","service_access__company__region"),
}
for model in (m.InspectionReport, m.JobCompletionCard, m.ServiceAppointment, m.JobAssignment,
              m.ServiceUpdate, m.ServiceAttachment, m.PendingServiceCharge, m.InternalPurchaseReport):
    REGION_PATHS[model] = ("job_equipment__job__region",)
report_paths = ("inspection_report__job_equipment__job__region", "completion_card__job_equipment__job__region")
REGION_PATHS[m.GeneratedDocument] = (*report_paths, "invoice__region", "quotation__region", "internal_purchase_report__job_equipment__job__region")
REGION_PATHS[m.DocumentApproval] = (*report_paths, "quotation__region")
REGION_PATHS[m.EmailApprovalSubmission] = tuple("source_document__" + path for path in report_paths)
REGION_PATHS[m.QuoteEmailRequest] = ("document__quotation__region",)

TECHNICIAN_PATHS = {
    m.EmployeeProfile: "user", m.ServiceJob: "machines__assigned_technician__user",
    m.JobEquipment: "assigned_technician__user", m.ServiceAppointment: "technician__user",
    m.InspectionReport: "technician__user", m.JobCompletionCard: "technician__user",
    m.JobAssignment: "technician__user", m.ServiceUpdate: "job_equipment__assigned_technician__user",
    m.ServiceAttachment: "job_equipment__assigned_technician__user", m.InspectionFinding: "report__technician__user",
    m.CompletionWorkItem: "card__technician__user", m.InternalPurchaseReport: "technician__user",
    m.InternalPurchaseReportLine: "report__technician__user",
}
GLOBAL_CONFIGURATION = (m.DocumentTemplate, m.AuditEvent)


def scoped(queryset, user):
    if user.is_superuser:
        return queryset
    employee = getattr(user, "employee", None)
    if not employee:
        return queryset.none()
    model = queryset.model
    if not employee.global_scope:
        paths = REGION_PATHS.get(model, ())
        if not paths:
            return queryset.none()
        where = Q()
        for path in paths:
            where |= Q(**{path: employee.region_id})
        queryset = queryset.filter(where).distinct()
    if model in TECHNICIAN_PATHS and user.groups.filter(name="Technician").exists():
        queryset = queryset.filter(**{TECHNICIAN_PATHS[model]: user.pk}).distinct()
    return queryset


class SuperuserManagement:
    # Staff cannot grant themselves privileges by editing users or groups.
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission

    def has_add_permission(self, request):
        return request.user.is_superuser


class ScopedAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return scoped(super().get_queryset(request), request.user)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        model = db_field.remote_field.model
        rows = kwargs.get("queryset", model._default_manager.all())
        kwargs["queryset"] = scoped(rows, request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_add_permission(self, request):
        employee = getattr(request.user, "employee", None)
        has_scope = request.user.is_superuser or (employee and (employee.global_scope or self.model in REGION_PATHS))
        return bool(has_scope and super().has_add_permission(request))

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if getattr(self.model, "created_by", None):
            fields.append("created_by")
        return fields

    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, "created_by_id"):
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class ReadOnlyAdmin(ScopedAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EmployeeAdmin(SuperuserManagement, ScopedAdmin):
    pass


class WorkflowAdmin(ScopedAdmin):
    tasks = ()
    readonly_fields = ("staff_actions",)
    list_display = ("__str__", "staff_actions")

    @admin.display(description="Operations")
    def staff_actions(self, obj):
        if not obj or not obj.pk:
            return ""
        return format_html_join(" · ", '<a href="{}">{}</a>',
            ((reverse("portal-workflow", args=(key, obj.pk)), label) for key, label in self.tasks))


class ReportAdmin(WorkflowAdmin):
    kind = "inspection"

    @property
    def tasks(self):
        return ((self.kind+"-submit","Submit for approval"), (self.kind+"-sign","Record technician signature"),
                (self.kind+"-tablet","Record on-site approval"), (self.kind+"-publish","Generate PDF"))

    def get_readonly_fields(self, request, obj=None):
        base = super().get_readonly_fields(request, obj)
        if obj and obj.status != "draft":
            return [*base, *(f.name for f in self.model._meta.fields if f.editable)]
        return [*base, "status", "finalized_at", "submitted_at"]

    def has_delete_permission(self, request, obj=None):
        return bool(obj and obj.status == "draft" and super().has_delete_permission(request, obj))


class CompletionAdmin(ReportAdmin):
    kind = "completion"


class CommercialAdmin(WorkflowAdmin):
    def get_readonly_fields(self, request, obj=None):
        base = super().get_readonly_fields(request, obj)
        if obj and obj.status != "draft":
            return [*base, *(f.name for f in self.model._meta.fields if f.editable)]
        return [*base, "status", "billing_snapshot", "subtotal", "total"]

    def has_delete_permission(self, request, obj=None):
        return bool(obj and obj.status == "draft" and super().has_delete_permission(request, obj))


class QuoteAdmin(CommercialAdmin):
    tasks = (("quotation-issue","Send quotation"),("quotation-publish","Generate PDF"),("quotation-reply","Verify acceptance by email"))


class InvoiceAdmin(CommercialAdmin):
    tasks = (("invoice-issue","Issue invoice"),("invoice-publish","Generate PDF"),("invoice-pay","Record payment"),
             ("invoice-credit","Issue credit note"),("invoice-void","Void invoice"))


class ChildOfDraftAdmin(ScopedAdmin):
    parents = {m.CompletionWorkItem: "card", m.InspectionFinding: "report", m.InvoiceLine: "invoice", m.QuotationLine: "quotation"}

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == self.parents[self.model]:
            kwargs["queryset"] = db_field.remote_field.model.objects.filter(status="draft")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_change_permission(self, request, obj=None):
        return (not obj or getattr(obj,self.parents[self.model]).status=="draft") and super().has_change_permission(request,obj)

    def has_delete_permission(self, request, obj=None):
        return bool(obj and getattr(obj,self.parents[self.model]).status=="draft" and super().has_delete_permission(request,obj))

    def save_model(self, request, obj, form, change):
        field = self.parents[self.model]
        parent = getattr(obj,field)
        if not type(parent).objects.select_for_update().filter(pk=parent.pk,status="draft").exists():
            raise PermissionDenied("Only draft documents accept line changes.")
        super().save_model(request,obj,form,change)


class RentalItemAdmin(ScopedAdmin):
    fields = ("region","name","code","category","description","icon","available","is_active")
    list_display = ("name","region","category","available","is_active")
    list_filter = ("region","is_active")
    search_fields = ("name","code")
    prepopulated_fields = {"code": ("name",)}

    def get_readonly_fields(self, request, obj=None):
        base = super().get_readonly_fields(request, obj)
        if obj and m.RentalOrderLine.objects.filter(item=obj).exists():
            return [*base,"region","code"]
        return base

    def get_prepopulated_fields(self, request, obj=None):
        return {} if obj else self.prepopulated_fields


class ReadOnlyOperations(WorkflowAdmin, ReadOnlyAdmin):
    pass


class RentalOrderAdmin(ReadOnlyOperations):
    tasks = (("rental-review","Record phone confirmation"),)
    list_display = ("id","region","company","status","created_at","staff_actions")


class RescheduleAdmin(ReadOnlyOperations):
    tasks = (("visit-review","Review requested date"),)
    list_display = ("id","appointment","proposed_date","status","staff_actions")


class PaymentAdmin(ReadOnlyOperations):
    tasks = (("payment-void","Void entry"),)


class ReturnedReportAdmin(WorkflowAdmin):
    tasks = (("report-return","Verify signed return"),)
    readonly_fields = ("status","verified_by","verified_at","signed_pdf_sha256","signer_snapshot","staff_actions")

    def has_change_permission(self, request, obj=None):
        return (obj is None or obj.status=="pending") and super().has_change_permission(request,obj)

    def has_delete_permission(self, request, obj=None):
        return False


class GroupAdmin(SuperuserManagement, admin.ModelAdmin):
    filter_horizontal = ("permissions",)
    search_fields = ("name",)


ADMIN_CLASSES = {
    m.InspectionReport: ReportAdmin, m.JobCompletionCard: CompletionAdmin, m.Invoice: InvoiceAdmin,
    m.Quotation: QuoteAdmin, m.Payment: PaymentAdmin, m.EmployeeProfile: EmployeeAdmin,
    m.RentalItem: RentalItemAdmin, m.RentalOrder: RentalOrderAdmin, m.RescheduleRequest: RescheduleAdmin,
    m.EmailApprovalSubmission: ReturnedReportAdmin, m.DocumentApproval: ReadOnlyAdmin,
    m.GeneratedDocument: ReadOnlyAdmin, m.Notification: ReadOnlyAdmin, m.NotificationDelivery: ReadOnlyAdmin,
    m.QuoteEmailRequest: ReadOnlyAdmin, m.RentalOrderLine: ReadOnlyAdmin,
    m.AMCMonthlySummary: ReadOnlyAdmin, m.ReminderOccurrence: ReadOnlyAdmin,
    **{model: ChildOfDraftAdmin for model in ChildOfDraftAdmin.parents},
}
