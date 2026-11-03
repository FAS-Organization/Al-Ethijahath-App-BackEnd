"""AL Ethijahath Phase 1 models, revision 2 — confirmed employee workflows.

Target app: web. settings.AUTH_USER_MODEL = 'web.User'.
This file replaces the earlier models.py; KEEP existing migration files/data.
Run check, makemigrations web, inspect the migration, then migrate after backup.
New links on existing tables are nullable for migration compatibility. Historical
records remain valid; new workflow services must populate those links.

CONFIRMED PHASE 1
* Manager assigns inspections/repair visits; technicians accept/decline.
* Technicians enter professional inspections, separate internal purchase reports,
  progress and completion cards through a restricted Django admin interface.
* Admin prepares versioned quotations; account holder accepts/rejects in portal.
  Email sends a notification/link, not a separate quotation approval channel.
* Account holder maintains named authorized approvers without separate logins.
* Inspection/completion: authorized person signs on tablet OR returns the FULL
  signed PDF by email. Sales Executive verifies and uploads emailed evidence.
* Purchasing, stock and repair-start coordination remain outside the application.
  Internal purchase reports do not reserve/deduct stock or authorize expenditure.
* Customer portal: equipment/history, AMC visits/rescheduling, partial payments,
  generated PDFs, notifications and scheduled reminders.

STILL REQUIRES APPLICATION CODE (models alone do not implement workflows)
* Restricted admin/querysets and APIs; role + region + assignment permissions.
  job_title/global_scope/is_staff are not substitutes for action permissions.
* Bind identity fields to request.user; never trust customer-submitted author IDs.
* All workflow writes call full_clean(); save(), update() and bulk_create() do not.
* Transactional assignment, acceptance/reassignment, invoice posting, payment
  posting, numbering, version creation and reminder deduplication. Use row locks.
* Lock submitted/approved document data AND children against edits/deletion,
  including admin/queryset writes. Create new versions with audit events.
* PDF generation/signature capture/private file storage, email and scheduled jobs.
* Publish completion only after technician + authorized customer approval;
  an uploaded email PDF is evidence awaiting verification, not automatic approval.
* Only Sales Executive's verify action may validate EmailApprovalSubmission;
  no permission derives merely from a typed job title. Save resulting approval
  and snapshots atomically; old approvals survive later signer deactivation.
* Validate version-linked evidence, amount/line totals, no double invoicing,
  no overpayment; corrections are audited entries, not silent changes/refunds.
* Existing assigned_technician/appointment.technician fields are compatibility
  summaries. New JobAssignment is history/source; update summaries atomically.
* Quotation cost lines and internal reports must never leak to customer APIs/PDFs.
* Migrate old historical approvals deliberately; legacy modes are retained only
  to preserve stored records, not to enable new unverified approval routes.

No self-claim, live inventory, purchasing, payroll, GPS, negotiation chat,
secure email approval tokens or automated mailbox ingestion in Phase 1.
"""

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum
from django.db.models.functions import Lower
from django.utils import timezone

ZERO = Decimal("0.00")


def private_upload_path(instance, filename):
    # Random paths reduce collisions; they do NOT replace authorization.
    extension = Path(filename).suffix.lower()[:12]
    return f"private/{instance._meta.model_name}/{uuid4().hex}{extension}"


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("Enter a valid IANA timezone, e.g. Asia/Riyadh.")


def exactly_one(*fields):
    """Database constraint expression requiring one non-null foreign key."""
    result = Q()
    for selected in fields:
        term = Q(**{f"{selected}__isnull": False})
        for other in fields:
            if other != selected:
                term &= Q(**{f"{other}__isnull": True})
        result |= term
    return result


def require_match(left, right, field, message):
    if left is not None and right is not None and left != right:
        raise ValidationError({field: message})


class Currency(models.TextChoices):
    SAR = "SAR", "Saudi riyal"
    AED = "AED", "UAE dirham"
    QAR = "QAR", "Qatari riyal"


class Visibility(models.TextChoices):
    INTERNAL = "internal", "Internal only"
    CUSTOMER = "customer", "Customer visible"


class DocumentStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING = "pending_approval", "Pending approval"
    APPROVED = "approved", "Approved"
    SUPERSEDED = "superseded", "Superseded"


class DocumentType(models.TextChoices):
    INSPECTION = "inspection", "Inspection report"
    COMPLETION = "completion", "Job completion card"
    INVOICE = "invoice", "Invoice"
    QUOTATION = "quotation", "Quotation"
    PURCHASE_REPORT = "purchase_report", "Internal purchase report"


class UserManager(DjangoUserManager):
    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        user = self.model(
            email=self.normalize_email(email).strip().lower(), **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("account_status", "active")
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("Superuser requires is_staff and is_superuser.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending invitation activation"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        REJECTED = "rejected", "Rejected"

    username = None
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=30, blank=True)
    account_status = models.CharField(
        max_length=20, choices=Status, default=Status.PENDING
    )
    is_active = models.BooleanField(default=False)
    preferred_language = models.CharField(
        max_length=2,
        choices=[("en", "English"), ("hi", "Hindi"), ("ar", "Arabic")],
        default="en",
    )
    timezone = models.CharField(
        max_length=64, default="Asia/Riyadh", validators=[validate_timezone]
    )
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="user_email_case_insensitive"),
            models.CheckConstraint(
                condition=Q(is_active=False) | Q(account_status="active"),
                name="active_login_requires_active_status",
            ),
        ]

    def __str__(self):
        return self.email


class Record(models.Model):
    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True


class Region(Record):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=12, unique=True)
    currency = models.CharField(max_length=3, choices=Currency)
    timezone = models.CharField(
        max_length=64, default="Asia/Riyadh", validators=[validate_timezone]
    )
    bank_details = models.TextField(blank=True)
    payment_qr = models.FileField(upload_to=private_upload_path, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class EmployeeProfile(Record):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee"
    )
    employee_id = models.CharField(max_length=40, unique=True)
    region = models.ForeignKey(Region, null=True, blank=True, on_delete=models.PROTECT)
    job_title = models.CharField(max_length=100)
    # This controls data scope, not permission to perform actions.
    global_scope = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(global_scope=True, region__isnull=True)
                | Q(global_scope=False, region__isnull=False),
                name="employee_global_or_single_region",
            )
        ]

    def __str__(self):
        return f"{self.employee_id}: {self.user}"


class CustomerCompany(Record):
    name = models.CharField(max_length=200)
    account_holder = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="customer_company",
    )
    registration_number = models.CharField(max_length=80, blank=True)
    tax_number = models.CharField(max_length=80, blank=True)
    billing_address = models.TextField()
    contact_name = models.CharField(max_length=150)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=30)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class CustomerSite(Record):
    company = models.ForeignKey(
        CustomerCompany, on_delete=models.PROTECT, related_name="sites"
    )
    region = models.ForeignKey(Region, on_delete=models.PROTECT)
    name = models.CharField(max_length=150)
    address = models.TextField()
    contact_name = models.CharField(max_length=150, blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.company}: {self.name}"


class DocumentNumberSequence(Record):
    region = models.ForeignKey(Region, on_delete=models.PROTECT)
    document_type = models.CharField(max_length=20, choices=DocumentType)
    prefix = models.CharField(max_length=30)
    next_number = models.PositiveBigIntegerField(
        default=1, validators=[MinValueValidator(1)]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["region", "document_type"],
                name="unique_region_document_sequence",
            )
        ]


class Equipment(Record):
    company = models.ForeignKey(
        CustomerCompany, on_delete=models.PROTECT, related_name="equipment"
    )
    current_site = models.ForeignKey(
        CustomerSite, on_delete=models.PROTECT, related_name="equipment"
    )
    equipment_type = models.CharField(max_length=100)
    brand = models.CharField(max_length=100, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=120, blank=True)
    customer_asset_number = models.CharField(max_length=100, blank=True)
    fuel_type = models.CharField(max_length=50, blank=True)
    capacity = models.CharField(max_length=80, blank=True)
    manufacture_year = models.PositiveSmallIntegerField(null=True, blank=True)
    photo = models.FileField(upload_to=private_upload_path, blank=True)
    is_retired = models.BooleanField(default=False)

    def clean(self):
        if self.current_site_id:
            require_match(
                self.company_id,
                self.current_site.company_id,
                "current_site",
                "Site belongs to another company.",
            )

    def __str__(self):
        return f"{self.equipment_type} {self.serial_number or self.pk}"


class AMCContract(Record):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(
        CustomerCompany, on_delete=models.PROTECT, related_name="amc_contracts"
    )
    contract_number = models.CharField(max_length=80, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    service_interval_months = models.PositiveSmallIntegerField(
        default=12, validators=[MinValueValidator(1)]
    )
    contract_file = models.FileField(upload_to=private_upload_path, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")), name="amc_valid_dates"
            )
        ]


class AMCEquipmentCoverage(Record):
    contract = models.ForeignKey(
        AMCContract, on_delete=models.PROTECT, related_name="equipment_coverages"
    )
    equipment = models.ForeignKey(
        Equipment, on_delete=models.PROTECT, related_name="amc_coverages"
    )
    internal_reference = models.CharField(max_length=50)
    start_date = models.DateField()
    end_date = models.DateField()
    interval_months_override = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)]
    )
    next_service_due_date = models.DateField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "equipment"], name="unique_contract_equipment"
            ),
            models.UniqueConstraint(
                fields=["contract", "internal_reference"],
                name="unique_contract_reference",
            ),
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")), name="coverage_valid_dates"
            ),
        ]

    def clean(self):
        if self.contract_id and self.equipment_id:
            require_match(
                self.contract.company_id,
                self.equipment.company_id,
                "equipment",
                "Equipment belongs to another company.",
            )
            if self.start_date and self.end_date:
                if (
                    self.start_date < self.contract.start_date
                    or self.end_date > self.contract.end_date
                ):
                    raise ValidationError(
                        "Coverage dates must be inside contract dates."
                    )
        # Service layer must reject overlapping active coverage for the same unit.


class ServiceJob(Record):
    class Kind(models.TextChoices):
        AMC = "amc", "AMC"
        SERVICE = "service", "Service"
        GENERAL = "general", "General"

    class Status(models.TextChoices):
        ENQUIRY = "enquiry", "Enquiry"
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Work completed"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    # The database-generated integer primary key is the parent Job ID.
    company = models.ForeignKey(
        CustomerCompany, on_delete=models.PROTECT, related_name="jobs"
    )
    site = models.ForeignKey(CustomerSite, on_delete=models.PROTECT)
    region = models.ForeignKey(Region, on_delete=models.PROTECT)
    site_address_snapshot = models.TextField(blank=True)
    job_type = models.CharField(max_length=12, choices=Kind)
    description = models.TextField()
    requested_date = models.DateField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    status = models.CharField(
        max_length=16, choices=Status, default=Status.ENQUIRY, db_index=True
    )
    cancellation_reason = models.TextField(blank=True)
    is_historical_import = models.BooleanField(default=False)
    external_reference = models.CharField(max_length=100, blank=True)

    def clean(self):
        if self.site_id:
            require_match(
                self.company_id,
                self.site.company_id,
                "site",
                "Site belongs to another company.",
            )
            require_match(
                self.region_id,
                self.site.region_id,
                "region",
                "Region must match the site.",
            )

    def __str__(self):
        return f"Job {self.pk}"


class JobEquipment(Record):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        INSPECTION = "inspection", "Inspection"
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
        QUOTATION = "quotation", "Quotation"
        QUOTATION_APPROVED = "quotation_approved", "Quotation approved"
        SCHEDULED = "scheduled", "Scheduled"
        AWAITING_PARTS = "awaiting_parts", "Awaiting parts"
        IN_PROGRESS = "in_progress", "In progress"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    job = models.ForeignKey(
        ServiceJob, on_delete=models.PROTECT, related_name="machines"
    )
    equipment = models.ForeignKey(
        Equipment, on_delete=models.PROTECT, related_name="service_records"
    )
    suffix = models.CharField(max_length=8)
    amc_coverage = models.ForeignKey(
        AMCEquipmentCoverage, null=True, blank=True, on_delete=models.PROTECT
    )
    assigned_technician = models.ForeignKey(
        EmployeeProfile, null=True, blank=True, on_delete=models.PROTECT
    )
    reported_issue = models.TextField(blank=True)
    planned_work = models.TextField(blank=True)
    status = models.CharField(
        max_length=24, choices=Status, default=Status.NOT_STARTED, db_index=True
    )
    estimated_completion = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    equipment_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["job", "suffix"], name="unique_job_suffix"),
            models.UniqueConstraint(
                fields=["job", "equipment"], name="unique_machine_per_job"
            ),
        ]

    @property
    def reference(self):
        return f"{self.job_id}{self.suffix}"

    def clean(self):
        if self.job_id and self.equipment_id:
            require_match(
                self.job.company_id,
                self.equipment.company_id,
                "equipment",
                "Equipment belongs to another company.",
            )
        if self.amc_coverage_id:
            require_match(
                self.equipment_id,
                self.amc_coverage.equipment_id,
                "amc_coverage",
                "Coverage belongs to another machine.",
            )

    def __str__(self):
        return self.reference


class ServiceAppointment(Record):
    class Purpose(models.TextChoices):
        INSPECTION = "inspection", "Inspection"
        MAINTENANCE = "maintenance", "Maintenance"
        REPAIR = "repair", "Repair"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        CONFIRMED = "confirmed", "Confirmed"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        MISSED = "missed", "Missed"

    job_equipment = models.ForeignKey(
        JobEquipment, on_delete=models.PROTECT, related_name="appointments"
    )
    purpose = models.CharField(max_length=16, choices=Purpose)
    original_due_date = models.DateField(null=True, blank=True)
    scheduled_start = models.DateTimeField(db_index=True)
    scheduled_end = models.DateTimeField()
    technician = models.ForeignKey(
        EmployeeProfile, null=True, blank=True, on_delete=models.PROTECT
    )
    status = models.CharField(max_length=16, choices=Status, default=Status.PLANNED)
    assignment = models.ForeignKey(
        "JobAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    schedule_version = models.PositiveIntegerField(default=1)
    actual_arrival = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(scheduled_end__gt=F("scheduled_start")),
                name="appointment_valid_times",
            )
        ]

    def clean(self):
        if self.assignment_id:
            require_match(
                self.job_equipment_id,
                self.assignment.job_equipment_id,
                "assignment",
                "Assignment belongs to another service.",
            )
            if self.technician_id != self.assignment.technician_id:
                raise ValidationError(
                    {"technician": "Technician must match assignment."}
                )
            expected = "inspection" if self.purpose == "inspection" else "repair"
            if self.assignment.purpose != expected:
                raise ValidationError(
                    {
                        "assignment": "Assignment visit purpose does not match appointment."
                    }
                )


class RescheduleRequest(Record):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        ALTERNATIVE = "alternative", "Alternative proposed"
        WITHDRAWN = "withdrawn", "Withdrawn"

    appointment = models.ForeignKey(
        ServiceAppointment, on_delete=models.PROTECT, related_name="reschedule_requests"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    original_start = models.DateTimeField()
    original_end = models.DateTimeField()
    appointment_version = models.PositiveIntegerField()
    proposed_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True)
    alternative_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["appointment"],
                condition=Q(status__in=["pending", "alternative"]),
                name="one_open_reschedule_per_visit",
            )
        ]


class ServiceUpdate(Record):
    job_equipment = models.ForeignKey(
        JobEquipment, on_delete=models.PROTECT, related_name="updates"
    )
    status = models.CharField(max_length=24, choices=JobEquipment.Status)
    message = models.TextField()
    visibility = models.CharField(
        max_length=12, choices=Visibility, default=Visibility.INTERNAL
    )
    estimated_completion = models.DateTimeField(null=True, blank=True)
    event_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-event_at", "-id"]


class VersionedServiceDocument(Record):
    job_equipment = models.ForeignKey(
        JobEquipment, on_delete=models.PROTECT, related_name="%(class)s_versions"
    )
    version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    previous_version = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="next_version",
    )
    status = models.CharField(
        max_length=20, choices=DocumentStatus, default=DocumentStatus.DRAFT
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["job_equipment", "version"], name="%(class)s_unique_version"
            )
        ]

    def clean(self):
        if self.previous_version_id:
            previous = self.previous_version
            if previous.pk == self.pk or previous.version >= self.version:
                raise ValidationError(
                    {"previous_version": "Previous version must be older."}
                )
            require_match(
                self.job_equipment_id,
                previous.job_equipment_id,
                "previous_version",
                "Must belong to the same service.",
            )


class InspectionReport(VersionedServiceDocument):
    class Purpose(models.TextChoices):
        CUSTOMER = "customer", "Customer inspection"
        INTERNAL = "internal", "Internal reinspection"

    technician = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT)
    inspected_at = models.DateTimeField()
    purpose = models.CharField(max_length=12, choices=Purpose, default=Purpose.CUSTOMER)
    remarks = models.TextField(blank=True)
    assignment = models.ForeignKey(
        "JobAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inspection_reports",
    )

    def clean(self):
        super().clean()
        if self.assignment_id:
            require_match(
                self.job_equipment_id,
                self.assignment.job_equipment_id,
                "assignment",
                "Assignment belongs to another service.",
            )
            require_match(
                self.technician_id,
                self.assignment.technician_id,
                "technician",
                "Technician must match the assignment.",
            )
            if self.assignment.purpose != "inspection":
                raise ValidationError({"assignment": "Inspection assignment required."})


class InspectionFinding(Record):
    class Condition(models.TextChoices):
        DAMAGED = "damaged", "Damaged"
        WORN = "worn", "Worn out"
        DEFECTIVE = "defective", "Defective"

    class Action(models.TextChoices):
        REPAIR = "repair", "Repair"
        REPLACE = "replace", "Replace"

    report = models.ForeignKey(
        InspectionReport, on_delete=models.PROTECT, related_name="findings"
    )
    component = models.CharField(max_length=200)
    part_number = models.CharField(max_length=100, blank=True)
    condition = models.CharField(max_length=12, choices=Condition)
    proposed_action = models.CharField(max_length=12, choices=Action)
    description = models.TextField(blank=True)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "id"]


class JobCompletionCard(VersionedServiceDocument):
    technician = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT)
    completed_at = models.DateTimeField()
    work_summary = models.TextField()
    final_condition = models.TextField()
    out_of_scope_notes = models.TextField(blank=True)
    assignment = models.ForeignKey(
        "JobAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="completion_cards",
    )

    def clean(self):
        super().clean()
        if self.assignment_id:
            require_match(
                self.job_equipment_id,
                self.assignment.job_equipment_id,
                "assignment",
                "Assignment belongs to another service.",
            )
            require_match(
                self.technician_id,
                self.assignment.technician_id,
                "technician",
                "Technician must match the assignment.",
            )
            if self.assignment.purpose != "repair":
                raise ValidationError(
                    {"assignment": "Repair/maintenance assignment required."}
                )


class CompletionWorkItem(Record):
    card = models.ForeignKey(
        JobCompletionCard, on_delete=models.PROTECT, related_name="work_items"
    )
    finding = models.ForeignKey(
        InspectionFinding, null=True, blank=True, on_delete=models.PROTECT
    )
    component = models.CharField(max_length=200)
    work_performed = models.TextField()
    action_taken = models.CharField(max_length=100)
    quantity_used = models.DecimalField(
        max_digits=12, decimal_places=3, default=0, validators=[MinValueValidator(ZERO)]
    )
    remarks = models.TextField(blank=True)

    def clean(self):
        if self.card_id and self.finding_id:
            require_match(
                self.card.job_equipment_id,
                self.finding.report.job_equipment_id,
                "finding",
                "Finding belongs to another service.",
            )


class DocumentApproval(Record):
    """An approval event for one exact source version, never for an entire job.

    Legacy offline/email_link values preserve earlier records only. New quotation
    decisions are in_app; report signatures are on_site or email_attachment.
    Rejected events also use approved_at as the legacy decision timestamp.
    """

    class Method(models.TextChoices):
        OFFLINE = "offline", "Legacy offline approval"
        IN_APP = "in_app", "Authenticated portal decision"
        ON_SITE = "on_site", "On-screen signature"
        EMAIL = "email_link", "Legacy secure email link"
        EMAIL_ATTACHMENT = "email_attachment", "Verified returned signed PDF"

    inspection_report = models.ForeignKey(
        InspectionReport,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approvals",
    )
    completion_card = models.ForeignKey(
        JobCompletionCard,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approvals",
    )
    quotation = models.ForeignKey(
        "Quotation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approvals",
    )
    signer_name = models.CharField(max_length=150)
    signer_email = models.EmailField(blank=True)
    signer_role = models.CharField(
        max_length=12, choices=[("technician", "Technician"), ("customer", "Customer")]
    )
    signing_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    customer_approver = models.ForeignKey(
        "CustomerApprover",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approvals",
    )
    authority_basis = models.TextField()
    method = models.CharField(max_length=20, choices=Method)
    decision = models.CharField(
        max_length=12, choices=[("approved", "Approved"), ("rejected", "Rejected")]
    )
    evidence_file = models.FileField(upload_to=private_upload_path, blank=True)
    approved_at = models.DateTimeField()
    reviewed_document = models.ForeignKey(
        "GeneratedDocument",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approval_events",
    )
    email_submission = models.OneToOneField(
        "EmailApprovalSubmission",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approval_event",
    )
    # Immutable snapshots populated by service at decision time.
    authority_snapshot = models.JSONField(default=dict, blank=True)
    document_sha256 = models.CharField(max_length=64, blank=True)
    # created_by identifies recorder/capturing technician, not necessarily signer.

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=exactly_one(
                    "inspection_report", "completion_card", "quotation"
                ),
                name="approval_exactly_one_document",
            ),
            models.CheckConstraint(
                condition=~Q(method="email_attachment")
                | Q(email_submission__isnull=False),
                name="email_approval_requires_evidence",
            ),
            models.CheckConstraint(
                condition=Q(email_submission__isnull=True)
                | Q(method="email_attachment"),
                name="email_evidence_matches_method",
            ),
            models.CheckConstraint(
                condition=Q(quotation__isnull=True)
                | Q(
                    method="in_app", signer_role="customer", signing_user__isnull=False
                ),
                name="quote_approval_portal_customer",
            ),
        ]

    def clean(self):
        sources = [
            self.inspection_report_id,
            self.completion_card_id,
            self.quotation_id,
        ]
        if sum(bool(value) for value in sources) != 1:
            raise ValidationError("Select exactly one source document.")
        source = self.inspection_report or self.completion_card or self.quotation
        company = source.job_equipment.job.company
        if self.customer_approver_id:
            require_match(
                company.pk,
                self.customer_approver.company_id,
                "customer_approver",
                "Approver belongs to another company.",
            )
        if self.signer_role == "technician":
            if self.quotation_id or self.customer_approver_id:
                raise ValidationError(
                    "Technician signatures apply only to their service reports."
                )
            if self.signing_user_id != source.technician.user_id:
                raise ValidationError(
                    {"signing_user": "Signature must be by the report technician."}
                )
        if (
            self._state.adding
            and not self.quotation_id
            and self.signer_role == "customer"
            and self.method
            not in ["offline", "email_link", "on_site", "email_attachment"]
        ):
            raise ValidationError(
                "Phase 1 report approval requires tablet signature or verified emailed PDF."
            )
        if self.quotation_id:
            if self.method != self.Method.IN_APP or self.signer_role != "customer":
                raise ValidationError(
                    "Quotations are approved/rejected by the account holder in the portal."
                )
            if (
                self.signing_user_id != company.account_holder_id
                or self.customer_approver_id
            ):
                raise ValidationError(
                    "Only the customer account holder can decide a quotation in Phase 1."
                )
        # Validate current authorization only for NEW events: later deactivation
        # must not invalidate already-recorded approvals.
        if (
            self._state.adding
            and self.signer_role == "customer"
            and self.method not in ["offline", "email_link"]
        ):
            if self.customer_approver_id:
                if not self.customer_approver.is_active:
                    raise ValidationError("Selected approver is inactive.")
                if self.signing_user_id:
                    raise ValidationError(
                        "Named approver has no separate login; do not attribute their signature to another user."
                    )
            elif self.signing_user_id != company.account_holder_id:
                raise ValidationError(
                    "Identify the account holder or a named authorized approver."
                )
        if self.reviewed_document_id:
            require_source_match(self, self.reviewed_document)
        if self.method == self.Method.EMAIL_ATTACHMENT:
            if not self.email_submission_id:
                raise ValidationError("Verified signed PDF submission is required.")
            evidence = self.email_submission
            require_source_match(self, evidence.source_document)
            if evidence.status != "verified" or not evidence.verified_by_id:
                raise ValidationError(
                    "Sales Executive must verify the signed PDF first."
                )
            if self.created_by_id != evidence.verified_by_id:
                raise ValidationError(
                    "Approval recorder must be the Sales Executive who verified it."
                )
            if (
                self.signing_user_id != evidence.signing_user_id
                or self.customer_approver_id != evidence.customer_approver_id
            ):
                raise ValidationError("Signer must match the verified email evidence.")
        if self.method == self.Method.ON_SITE and not self.evidence_file:
            raise ValidationError(
                {"evidence_file": "Captured signature image is required."}
            )


def require_source_match(approval, generated):
    """Check identity of version, not just matching equipment/job."""
    for field in ["inspection_report_id", "completion_card_id", "quotation_id"]:
        if getattr(approval, field) != getattr(generated, field):
            raise ValidationError(
                "Evidence must refer to the exact same document version."
            )
    if generated.internal_purchase_report_id:
        raise ValidationError(
            "Internal purchase reports never receive customer approvals."
        )


class Invoice(Record):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        VOID = "void", "Void"

    company = models.ForeignKey(
        CustomerCompany, on_delete=models.PROTECT, related_name="invoices"
    )
    job = models.ForeignKey(
        ServiceJob, on_delete=models.PROTECT, related_name="invoices"
    )
    region = models.ForeignKey(Region, on_delete=models.PROTECT)
    number = models.CharField(max_length=80, null=True, blank=True)
    currency = models.CharField(max_length=3, choices=Currency)
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    billing_snapshot = models.JSONField(default=dict, blank=True)
    subtotal = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(ZERO)]
    )
    discount = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(ZERO)]
    )
    tax_amount = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(ZERO)]
    )
    total = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(ZERO)]
    )
    status = models.CharField(
        max_length=12, choices=Status, default=Status.DRAFT, db_index=True
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["region", "number"], name="unique_regional_invoice_number"
            ),
            models.CheckConstraint(
                condition=Q(
                    subtotal__gte=0, discount__gte=0, tax_amount__gte=0, total__gte=0
                ),
                name="invoice_nonnegative_amounts",
            ),
            models.CheckConstraint(
                condition=Q(discount__lte=F("subtotal")),
                name="invoice_discount_within_subtotal",
            ),
            models.CheckConstraint(
                condition=Q(total=F("subtotal") - F("discount") + F("tax_amount")),
                name="invoice_total_matches_components",
            ),
            models.CheckConstraint(
                condition=~Q(status="issued")
                | (Q(number__isnull=False, issued_at__isnull=False) & ~Q(number="")),
                name="issued_invoice_has_number_date",
            ),
            models.CheckConstraint(
                condition=Q(due_date__gte=F("issue_date")),
                name="invoice_valid_due_date",
            ),
        ]
        indexes = [models.Index(fields=["status", "due_date"])]

    def clean(self):
        if self.job_id:
            require_match(
                self.company_id,
                self.job.company_id,
                "company",
                "Company must match job.",
            )
            require_match(
                self.region_id, self.job.region_id, "region", "Region must match job."
            )
        if self.region_id:
            require_match(
                self.currency,
                self.region.currency,
                "currency",
                "Currency must match region.",
            )

    @property
    def amount_paid(self):
        if not self.pk:
            return ZERO
        return (
            self.payments.filter(status=Payment.Status.POSTED).aggregate(
                total=Sum("amount")
            )["total"]
            or ZERO
        )

    @property
    def balance_due(self):
        return self.total - self.amount_paid

    @property
    def payment_status(self):
        if self.status != self.Status.ISSUED:
            return "not_payable"
        paid = self.amount_paid
        if paid >= self.total:
            return "paid"
        return "partially_paid" if paid > ZERO else "unpaid"

    @property
    def is_overdue(self):
        today = timezone.localdate(timezone=ZoneInfo(self.region.timezone))
        return (
            self.status == self.Status.ISSUED
            and self.due_date < today
            and self.balance_due > ZERO
        )

    def __str__(self):
        return self.number or f"Draft invoice {self.pk}"


class InvoiceLine(Record):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="lines")
    job_equipment = models.ForeignKey(
        JobEquipment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invoice_lines",
    )
    quotation_line = models.ForeignKey(
        "QuotationLine",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invoice_lines",
    )
    category = models.CharField(
        max_length=30,
        choices=[
            ("service", "Service"),
            ("part", "Part"),
            ("labor", "Labor"),
            ("transport", "Transport"),
            ("other", "Other"),
        ],
    )
    description = models.TextField()
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price = models.DecimalField(
        max_digits=16, decimal_places=2, validators=[MinValueValidator(ZERO)]
    )
    # Rounded to currency precision by the invoice service when issuing.
    line_total = models.DecimalField(
        max_digits=16, decimal_places=2, validators=[MinValueValidator(ZERO)]
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0, unit_price__gte=0, line_total__gte=0),
                name="invoice_line_valid_amounts",
            )
        ]

    def clean(self):
        if (
            self.quantity is not None
            and self.unit_price is not None
            and self.line_total is not None
        ):
            expected = (Decimal(self.quantity) * Decimal(self.unit_price)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if Decimal(self.line_total) != expected:
                raise ValidationError(
                    {
                        "line_total": "Must equal quantity times unit price, rounded to 2 decimals."
                    }
                )
        if self.invoice_id and self.job_equipment_id:
            require_match(
                self.invoice.job_id,
                self.job_equipment.job_id,
                "job_equipment",
                "Machine must belong to invoice job.",
            )
        if self.quotation_line_id:
            quote = self.quotation_line.quotation
            if (
                not self.job_equipment_id
                or quote.job_equipment_id != self.job_equipment_id
            ):
                raise ValidationError(
                    {
                        "quotation_line": "Quotation line must belong to the invoiced machine."
                    }
                )
            if quote.status != "approved":
                raise ValidationError(
                    {"quotation_line": "Invoice source quotation must be approved."}
                )


class Payment(Record):
    class Status(models.TextChoices):
        POSTED = "posted", "Posted"
        VOID = "void", "Voided entry (not a refund)"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.DecimalField(
        max_digits=16, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    paid_at = models.DateTimeField()
    method = models.CharField(
        max_length=20,
        choices=[
            ("cash", "Cash"),
            ("bank_transfer", "Bank transfer"),
            ("qr", "QR transfer"),
            ("other", "Other"),
        ],
    )
    transaction_reference = models.CharField(max_length=150, blank=True)
    evidence_file = models.FileField(upload_to=private_upload_path, blank=True)
    status = models.CharField(max_length=8, choices=Status, default=Status.POSTED)
    idempotency_key = models.UUIDField(default=uuid4, unique=True, editable=False)
    corrects = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replacement",
    )
    void_reason = models.TextField(blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="payment_positive_amount"
            ),
            models.CheckConstraint(
                condition=~Q(status="void")
                | (
                    Q(voided_at__isnull=False, voided_by__isnull=False)
                    & ~Q(void_reason="")
                ),
                name="void_payment_has_reason_actor",
            ),
        ]

    def clean(self):
        if self.corrects_id:
            if self.corrects_id == self.pk:
                raise ValidationError({"corrects": "Payment cannot correct itself."})
            require_match(
                self.invoice_id,
                self.corrects.invoice_id,
                "corrects",
                "Correction must use the same invoice.",
            )
        # Posting service must lock invoice, reject overpayment and non-issued
        # invoices, and reuse request idempotency_key for retries.


class DocumentTemplate(Record):
    document_type = models.CharField(max_length=20, choices=DocumentType)
    version = models.PositiveIntegerField(default=1)
    template_key = models.CharField(
        max_length=150, help_text="Identifier of a developer-maintained template."
    )
    fixed_content = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document_type", "version"],
                name="unique_document_template_version",
            ),
            models.UniqueConstraint(
                fields=["document_type"],
                condition=Q(is_active=True),
                name="one_active_template_per_type",
            ),
        ]


class GeneratedDocument(Record):
    inspection_report = models.ForeignKey(
        InspectionReport,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pdfs",
    )
    completion_card = models.ForeignKey(
        JobCompletionCard,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pdfs",
    )
    invoice = models.ForeignKey(
        Invoice, null=True, blank=True, on_delete=models.PROTECT, related_name="pdfs"
    )
    quotation = models.ForeignKey(
        "Quotation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pdfs",
    )
    internal_purchase_report = models.ForeignKey(
        "InternalPurchaseReport",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pdfs",
    )
    template = models.ForeignKey(DocumentTemplate, on_delete=models.PROTECT)
    document_number = models.CharField(max_length=80)
    data_snapshot = models.JSONField(default=dict)
    file = models.FileField(upload_to=private_upload_path, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=12,
        choices=[("pending", "Pending"), ("ready", "Ready"), ("failed", "Failed")],
        default="pending",
    )
    generation_error = models.TextField(blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    visibility = models.CharField(
        max_length=12, choices=Visibility, default=Visibility.INTERNAL
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=exactly_one(
                    "inspection_report",
                    "completion_card",
                    "invoice",
                    "quotation",
                    "internal_purchase_report",
                ),
                name="pdf_exactly_one_source",
            )
        ]

    def clean(self):
        sources = {
            "inspection": self.inspection_report_id,
            "completion": self.completion_card_id,
            "invoice": self.invoice_id,
            "quotation": self.quotation_id,
            "purchase_report": self.internal_purchase_report_id,
        }
        if sum(bool(value) for value in sources.values()) != 1:
            raise ValidationError("A generated PDF must have exactly one source.")
        expected = next(key for key, value in sources.items() if value)
        if self.internal_purchase_report_id and self.visibility != "internal":
            raise ValidationError(
                {"visibility": "Purchase report PDFs must remain internal."}
            )
        if self.template_id and self.template.document_type != expected:
            raise ValidationError(
                {"template": "Template type must match source document."}
            )
        if (
            self.inspection_report_id
            and self.inspection_report.purpose == "internal"
            and self.visibility == "customer"
        ):
            raise ValidationError(
                {
                    "visibility": "Internal reinspections cannot be published to customers."
                }
            )


class ServiceAttachment(Record):
    job_equipment = models.ForeignKey(
        JobEquipment, on_delete=models.PROTECT, related_name="attachments"
    )
    update = models.ForeignKey(
        ServiceUpdate,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="attachments",
    )
    finding = models.ForeignKey(
        InspectionFinding,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="attachments",
    )
    completion_card = models.ForeignKey(
        JobCompletionCard,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="attachments",
    )
    file = models.FileField(upload_to=private_upload_path)
    category = models.CharField(max_length=30, default="photo")
    caption = models.CharField(max_length=250, blank=True)
    visibility = models.CharField(
        max_length=12, choices=Visibility, default=Visibility.INTERNAL
    )

    def clean(self):
        if (
            sum(
                bool(v)
                for v in [self.update_id, self.finding_id, self.completion_card_id]
            )
            > 1
        ):
            raise ValidationError(
                "Choose at most one related update, finding or completion card."
            )
        targets = []
        if self.update_id:
            targets.append(self.update.job_equipment_id)
        if self.finding_id:
            targets.append(self.finding.report.job_equipment_id)
        if self.completion_card_id:
            targets.append(self.completion_card.job_equipment_id)
        for target in targets:
            require_match(
                self.job_equipment_id,
                target,
                "job_equipment",
                "Attachment source belongs to another service.",
            )


class Notification(Record):
    class Event(models.TextChoices):
        ASSIGNMENT = "assignment", "Technician assigned"
        ASSIGNMENT_RESPONSE = "assignment_response", "Assignment accepted/declined"
        QUOTATION_SENT = "quotation_sent", "Quotation sent"
        QUOTATION_DECISION = "quotation_decision", "Quotation accepted/rejected"
        QUOTATION_REMINDER = "quotation_reminder", "Quotation reminder"
        REPORT_APPROVAL = "report_approval", "Report approval requested"
        REPORT_APPROVED = "report_approved", "Report approved"
        UPCOMING_SERVICE = "upcoming_service", "Upcoming service"
        SERVICE_COMPLETED = "service_completed", "Service completed"
        INVOICE_ISSUED = "invoice_issued", "Invoice issued"
        PAYMENT_OVERDUE = "payment_overdue", "Payment overdue"
        PAYMENT_RECORDED = "payment_recorded", "Payment recorded"
        RESCHEDULE_DECISION = "reschedule_decision", "Reschedule decision"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notifications"
    )
    event_type = models.CharField(max_length=30, choices=Event)
    title = models.CharField(max_length=200)
    message = models.TextField()
    appointment = models.ForeignKey(
        ServiceAppointment, null=True, blank=True, on_delete=models.PROTECT
    )
    job_equipment = models.ForeignKey(
        JobEquipment, null=True, blank=True, on_delete=models.PROTECT
    )
    invoice = models.ForeignKey(
        Invoice, null=True, blank=True, on_delete=models.PROTECT
    )
    assignment = models.ForeignKey(
        "JobAssignment", null=True, blank=True, on_delete=models.PROTECT
    )
    quotation = models.ForeignKey(
        "Quotation", null=True, blank=True, on_delete=models.PROTECT
    )
    document = models.ForeignKey(
        GeneratedDocument, null=True, blank=True, on_delete=models.PROTECT
    )
    destination_path = models.CharField(max_length=300, blank=True)
    event_key = models.CharField(
        max_length=200, help_text="Stable key reused on retries, unique per recipient."
    )
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "event_key"], name="unique_recipient_event"
            )
        ]
        indexes = [models.Index(fields=["recipient", "read_at"])]


class NotificationDelivery(Record):
    notification = models.ForeignKey(
        Notification, on_delete=models.PROTECT, related_name="deliveries"
    )
    channel = models.CharField(
        max_length=12, choices=[("email", "Email"), ("in_app", "In app")]
    )
    destination = models.CharField(max_length=254)
    status = models.CharField(
        max_length=12,
        choices=[
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("sent", "Sent"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="pending",
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_reference = models.CharField(max_length=200, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "channel"], name="unique_notification_channel"
            )
        ]
        indexes = [models.Index(fields=["status", "next_retry_at"])]


class ReminderPolicy(Record):
    # One policy per region/type keeps configuration simple for Phase 1.
    region = models.ForeignKey(Region, on_delete=models.PROTECT)
    reminder_type = models.CharField(
        max_length=24,
        choices=[
            ("upcoming_service", "Upcoming service"),
            ("payment_overdue", "Overdue invoice"),
            ("quotation_pending", "Unanswered quotation"),
        ],
    )
    days_offset = models.IntegerField(
        help_text="Service: days before visit (negative). Payment: days after due date. Quotation: days after sent date; stop at expiry."
    )
    repeat_interval_days = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)]
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["region", "reminder_type"], name="unique_region_reminder_policy"
            )
        ]


class ReminderOccurrence(Record):
    policy = models.ForeignKey(ReminderPolicy, on_delete=models.PROTECT)
    appointment = models.ForeignKey(
        ServiceAppointment, null=True, blank=True, on_delete=models.PROTECT
    )
    invoice = models.ForeignKey(
        Invoice, null=True, blank=True, on_delete=models.PROTECT
    )
    quotation = models.ForeignKey(
        "Quotation", null=True, blank=True, on_delete=models.PROTECT
    )
    appointment_version = models.PositiveIntegerField(null=True, blank=True)
    scheduled_at = models.DateTimeField(db_index=True)
    occurrence_key = models.CharField(max_length=200, unique=True)
    status = models.CharField(
        max_length=12,
        choices=[
            ("pending", "Pending"),
            ("processed", "Processed"),
            ("cancelled", "Cancelled"),
        ],
        default="pending",
    )
    notification = models.OneToOneField(
        Notification, null=True, blank=True, on_delete=models.PROTECT
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=exactly_one("appointment", "invoice", "quotation"),
                name="reminder_exactly_one_target",
            )
        ]
        indexes = [models.Index(fields=["status", "scheduled_at"])]

    def clean(self):
        if self.policy_id:
            if (
                self.policy.reminder_type == "upcoming_service"
                and not self.appointment_id
            ):
                raise ValidationError("Service reminder requires an appointment.")
            if self.policy.reminder_type == "payment_overdue" and not self.invoice_id:
                raise ValidationError("Payment reminder requires an invoice.")

            if (
                self.policy.reminder_type == "quotation_pending"
                and not self.quotation_id
            ):
                raise ValidationError("Quotation reminder requires a quotation.")


class AuditEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=80)
    model_label = models.CharField(max_length=100)
    record_id = models.CharField(max_length=64)
    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    changes = models.JSONField(
        default=dict, help_text="Sanitized before/after values; never secrets."
    )
    reason = models.TextField(blank=True)
    source = models.CharField(
        max_length=12,
        choices=[
            ("admin", "Admin"),
            ("api", "API"),
            ("task", "Background task"),
            ("import", "Import"),
        ],
    )

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [models.Index(fields=["model_label", "record_id"])]
        # Append-only enforcement and write authorization belong in service/admin
        # code and, where required, restricted DB roles/triggers.


class CustomerApprover(Record):
    """Named company-authorized signer; no login account is created."""

    company = models.ForeignKey(
        CustomerCompany, on_delete=models.PROTECT, related_name="authorized_approvers"
    )
    name = models.CharField(max_length=150)
    email = models.EmailField()
    designation = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                "company", Lower("email"), name="unique_company_approver_email"
            )
        ]
        ordering = ["name", "id"]

    def __str__(self):
        return f"{self.name} <{self.email}>"


class JobAssignment(Record):
    """One dispatch per machine/visit purpose, with explicit response/history.

    Manager creates assignments. Reassignment ends the old row and creates a new
    one. No self-claim. Repair readiness/authorization is coordinated externally.
    Services enforce assigned_by/technician permissions and state transitions.
    """

    class Purpose(models.TextChoices):
        INSPECTION = "inspection", "Inspection visit"
        REPAIR = "repair", "Repair / maintenance visit"

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting technician response"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        REASSIGNED = "reassigned", "Reassigned"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    job_equipment = models.ForeignKey(
        JobEquipment, on_delete=models.PROTECT, related_name="assignments"
    )
    technician = models.ForeignKey(
        EmployeeProfile, on_delete=models.PROTECT, related_name="job_assignments"
    )
    purpose = models.CharField(max_length=12, choices=Purpose)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    assigned_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    responded_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.TextField(blank=True)
    previous_assignment = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replacement_assignment",
    )
    instructions = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_equipment", "purpose"],
                condition=Q(status__in=["pending", "accepted"]),
                name="one_open_assignment_per_purpose",
            ),
            models.CheckConstraint(
                condition=~Q(status="declined")
                | (Q(responded_at__isnull=False) & ~Q(decline_reason="")),
                name="decline_has_reason_and_time",
            ),
            models.CheckConstraint(
                condition=~Q(status="accepted") | Q(responded_at__isnull=False),
                name="accepted_assignment_has_time",
            ),
        ]
        indexes = [models.Index(fields=["technician", "status"])]
        permissions = [
            ("assign_technician", "Can assign technicians"),
            ("respond_to_assignment", "Can respond to own assignments"),
        ]

    def clean(self):
        if self.status == self.Status.DECLINED and not self.decline_reason.strip():
            raise ValidationError({"decline_reason": "A decline reason is required."})
        if self.previous_assignment_id:
            old = self.previous_assignment
            if old.pk == self.pk:
                raise ValidationError("An assignment cannot replace itself.")
            require_match(
                self.job_equipment_id,
                old.job_equipment_id,
                "previous_assignment",
                "Replacement must use the same machine job.",
            )
            require_match(
                self.purpose,
                old.purpose,
                "purpose",
                "Replacement must have the same visit purpose.",
            )
            if old.status not in ["declined", "reassigned", "cancelled"]:
                raise ValidationError(
                    "End the previous assignment before replacing it."
                )

    def __str__(self):
        return f"{self.job_equipment.reference}: {self.purpose} / {self.technician}"


class InternalPurchaseReport(Record):
    """Separate INTERNAL document, never a customer inspection/approval record.

    Original version is authored/submitted by the assigned technician. If Admin
    reconciles excluded scope after final quotation approval, create a new version
    linked to that quotation, keeping author identity and the technician original.
    No stock reads, inventory deduction, purchaser queue or budget enforcement in
    this release. In-stock designation is manually reported, not stock verified.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        SUPERSEDED = "superseded", "Superseded"

    job_equipment = models.ForeignKey(
        JobEquipment, on_delete=models.PROTECT, related_name="purchase_reports"
    )
    inspection_report = models.ForeignKey(
        InspectionReport, on_delete=models.PROTECT, related_name="purchase_reports"
    )
    technician = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT)
    assignment = models.ForeignKey(
        JobAssignment, on_delete=models.PROTECT, related_name="purchase_reports"
    )
    version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    previous_version = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="next_version",
    )
    status = models.CharField(max_length=12, choices=Status, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    change_reason = models.TextField(blank=True)
    reconciled_quotation = models.ForeignKey(
        "Quotation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reconciled_purchase_reports",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_equipment", "version"],
                name="unique_purchase_report_version",
            ),
            models.CheckConstraint(
                condition=~Q(status="submitted") | Q(submitted_at__isnull=False),
                name="purchase_submission_has_time",
            ),
        ]

    def clean(self):
        if self.inspection_report_id:
            require_match(
                self.job_equipment_id,
                self.inspection_report.job_equipment_id,
                "inspection_report",
                "Inspection must belong to the same service.",
            )
        if self.assignment_id:
            require_match(
                self.job_equipment_id,
                self.assignment.job_equipment_id,
                "assignment",
                "Assignment must belong to the same service.",
            )
            require_match(
                self.technician_id,
                self.assignment.technician_id,
                "technician",
                "Technician must match the assignment.",
            )
            if self.assignment.purpose != "inspection":
                raise ValidationError(
                    "Purchase report must originate from an inspection assignment."
                )
        if self.previous_version_id:
            old = self.previous_version
            require_match(
                self.job_equipment_id,
                old.job_equipment_id,
                "previous_version",
                "Previous version must belong to the same service.",
            )
            if old.pk == self.pk or old.version >= self.version:
                raise ValidationError("Previous report must be an earlier version.")
            if not self.change_reason.strip():
                raise ValidationError(
                    {"change_reason": "Explain why a new version was created."}
                )
        if self.reconciled_quotation_id:
            require_match(
                self.job_equipment_id,
                self.reconciled_quotation.job_equipment_id,
                "reconciled_quotation",
                "Quotation must belong to the same service.",
            )
            if self.reconciled_quotation.status != "approved":
                raise ValidationError(
                    "Only the final approved quotation can be used for reconciliation."
                )

    def __str__(self):
        return (
            f"Internal purchase report {self.job_equipment.reference} v{self.version}"
        )


class InternalPurchaseReportLine(Record):
    class Designation(models.TextChoices):
        ORDER = "order", "Order"
        REPAIR_LOCALLY = "repair_locally", "Repair locally (raw materials)"
        IN_STOCK = "in_stock", "In stock (manually reported)"

    report = models.ForeignKey(
        InternalPurchaseReport, on_delete=models.PROTECT, related_name="lines"
    )
    finding = models.ForeignKey(
        InspectionFinding,
        on_delete=models.PROTECT,
        related_name="purchase_report_lines",
    )
    part_name = models.CharField(max_length=200)
    part_number = models.CharField(max_length=100, blank=True)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    designation = models.CharField(max_length=16, choices=Designation)
    materials_description = models.TextField(blank=True)
    remarks = models.TextField(blank=True)
    previous_line = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="purchase_line_positive_quantity"
            )
        ]

    def clean(self):
        if self.report_id and self.finding_id:
            require_match(
                self.report.inspection_report_id,
                self.finding.report_id,
                "finding",
                "Finding must belong to the source inspection version.",
            )
        if self.previous_line_id:
            old = self.previous_line
            if old.pk == self.pk:
                raise ValidationError("A line cannot reference itself.")
            if (
                not self.report.previous_version_id
                or old.report_id != self.report.previous_version_id
            ):
                raise ValidationError(
                    "Previous line must come from the preceding report version."
                )
            if (
                self.report.reconciled_quotation_id
                and old.designation != self.designation
            ):
                raise ValidationError(
                    "Admin reconciliation cannot change procurement designation."
                )
        # Reconciliation service must preserve mapping for retained rows and
        # prevent bypassing designation protection by recreating retained lines.


class Quotation(Record):
    """One versioned quotation per machine; prices sourced from professional inspection.

    Customer PDF exposes customer_summary, total, currency and estimated time,
    not internal costs/profit breakdown. Tax is a manually entered amount.
    Initial validity is 7 days after sending; expiry/renewal handled by services.
    Portal Accept/Reject only in Phase 1. No negotiation messaging module.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
        SUPERSEDED = "superseded", "Superseded"

    job_equipment = models.ForeignKey(
        JobEquipment, on_delete=models.PROTECT, related_name="quotations"
    )
    inspection_report = models.ForeignKey(
        InspectionReport, on_delete=models.PROTECT, related_name="quotations"
    )
    region = models.ForeignKey(Region, on_delete=models.PROTECT)
    currency = models.CharField(max_length=3, choices=Currency)
    number = models.CharField(max_length=80, null=True, blank=True)
    version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    previous_version = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="next_version",
    )
    status = models.CharField(
        max_length=12, choices=Status, default=Status.DRAFT, db_index=True
    )
    customer_summary = models.TextField()
    estimated_completion_text = models.CharField(max_length=250)
    terms = models.TextField(blank=True)
    billing_snapshot = models.JSONField(default=dict, blank=True)
    subtotal = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(ZERO)]
    )
    discount = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(ZERO)]
    )
    tax_amount = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(ZERO)]
    )
    total = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(ZERO)]
    )
    internal_notes = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job_equipment", "version"],
                name="unique_machine_quotation_version",
            ),
            models.UniqueConstraint(
                fields=["region", "number", "version"],
                name="unique_regional_quote_number_version",
            ),
            models.UniqueConstraint(
                fields=["job_equipment"],
                condition=Q(status="approved"),
                name="one_approved_quote_per_machine_job",
            ),
            models.UniqueConstraint(
                fields=["job_equipment"],
                condition=Q(status="sent"),
                name="one_sent_quote_per_machine_job",
            ),
            models.CheckConstraint(
                condition=Q(
                    subtotal__gte=0, discount__gte=0, tax_amount__gte=0, total__gte=0
                ),
                name="quotation_nonnegative_amounts",
            ),
            models.CheckConstraint(
                condition=Q(discount__lte=F("subtotal")),
                name="quote_discount_within_subtotal",
            ),
            models.CheckConstraint(
                condition=Q(total=F("subtotal") - F("discount") + F("tax_amount")),
                name="quote_total_matches_components",
            ),
            models.CheckConstraint(
                condition=Q(sent_at__isnull=True, expires_at__isnull=True)
                | Q(sent_at__isnull=False, expires_at__gt=F("sent_at")),
                name="quote_valid_expiry",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=["sent", "approved", "rejected", "expired"])
                | (
                    Q(
                        sent_at__isnull=False,
                        expires_at__isnull=False,
                        number__isnull=False,
                    )
                    & ~Q(number="")
                ),
                name="sent_quote_has_number_dates",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=["approved", "rejected"])
                | Q(decided_at__isnull=False),
                name="quote_decision_has_timestamp",
            ),
        ]

    def clean(self):
        if self.job_equipment_id:
            require_match(
                self.region_id,
                self.job_equipment.job.region_id,
                "region",
                "Quotation region must match the job.",
            )
        if self.region_id:
            require_match(
                self.currency,
                self.region.currency,
                "currency",
                "Currency must match the region.",
            )
        if self.inspection_report_id:
            report = self.inspection_report
            require_match(
                self.job_equipment_id,
                report.job_equipment_id,
                "inspection_report",
                "Inspection must belong to the same service.",
            )
            if report.purpose != "customer":
                raise ValidationError(
                    "Internal reinspection cannot define quotation scope."
                )
            if report.status not in ["approved", "superseded"]:
                raise ValidationError(
                    "Quotation requires a previously approved professional inspection."
                )
        if self.previous_version_id:
            old = self.previous_version
            require_match(
                self.job_equipment_id,
                old.job_equipment_id,
                "previous_version",
                "Quotation must belong to the same service.",
            )
            if old.pk == self.pk or old.version >= self.version:
                raise ValidationError("Previous quotation must be an earlier version.")
            if old.status == "approved":
                raise ValidationError(
                    "Approved quotation is locked; genuinely new damage needs a new scope cycle."
                )

    @property
    def is_expired(self):
        return bool(
            self.status == self.Status.SENT
            and self.expires_at
            and timezone.now() >= self.expires_at
        )

    def __str__(self):
        return f'{self.number or "Draft quotation"} v{self.version}'


class QuotationLine(Record):
    quotation = models.ForeignKey(
        Quotation, on_delete=models.PROTECT, related_name="lines"
    )
    finding = models.ForeignKey(
        InspectionFinding,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="quotation_lines",
    )
    category = models.CharField(
        max_length=20,
        choices=[
            ("part", "Part"),
            ("labor", "Labor"),
            ("transport", "Transportation"),
            ("misc", "Miscellaneous"),
            ("service", "Service"),
        ],
    )
    description = models.TextField()
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    # Unit COST and customer-facing unit PRICE are separate. Profit is the
    # difference; do not expose internal_unit_cost in customer serializers.
    internal_unit_cost = models.DecimalField(
        max_digits=16, decimal_places=2, default=0, validators=[MinValueValidator(ZERO)]
    )
    unit_price = models.DecimalField(
        max_digits=16, decimal_places=2, validators=[MinValueValidator(ZERO)]
    )
    line_total = models.DecimalField(
        max_digits=16, decimal_places=2, validators=[MinValueValidator(ZERO)]
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    quantity__gt=0,
                    internal_unit_cost__gte=0,
                    unit_price__gte=0,
                    line_total__gte=0,
                ),
                name="quote_line_valid_amounts",
            )
        ]

    def clean(self):
        if (
            self.quantity is not None
            and self.unit_price is not None
            and self.line_total is not None
        ):
            expected = (Decimal(self.quantity) * Decimal(self.unit_price)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if Decimal(self.line_total) != expected:
                raise ValidationError(
                    {
                        "line_total": "Must equal quantity times unit price, rounded to 2 decimals."
                    }
                )
        if self.finding_id and self.quotation_id:
            require_match(
                self.finding.report_id,
                self.quotation.inspection_report_id,
                "finding",
                "Finding must belong to the quotation source inspection version.",
            )


class EmailApprovalSubmission(Record):
    """Full signed PDF returned by email; verified MANUALLY by Sales Executive.

    source_document references the exact original PDF emailed to the customer.
    Keep email evidence (e.g. .eml) and sender details as well as the returned PDF.
    No automatic mailbox ingestion or 'signature image only' approval in Phase 1.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Sales Executive verification"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected evidence"

    source_document = models.ForeignKey(
        GeneratedDocument, on_delete=models.PROTECT, related_name="email_returns"
    )
    signing_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    customer_approver = models.ForeignKey(
        CustomerApprover,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="email_returns",
    )
    signer_name = models.CharField(max_length=150)
    signer_email = models.EmailField()
    sender_email = models.EmailField()
    email_subject = models.CharField(max_length=250, blank=True)
    email_message_id = models.CharField(max_length=250, blank=True)
    email_evidence = models.FileField(upload_to=private_upload_path)
    received_at = models.DateTimeField()
    signed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Only enter if known; do not infer signature time from email receipt.",
    )
    signed_pdf = models.FileField(upload_to=private_upload_path)
    signed_pdf_sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)
    signer_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        permissions = [
            (
                "verify_email_approval",
                "Can verify returned signed PDFs as Sales Executive",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=exactly_one("signing_user", "customer_approver"),
                name="email_return_exactly_one_signer",
            ),
            models.CheckConstraint(
                condition=~Q(status="verified")
                | Q(verified_by__isnull=False, verified_at__isnull=False),
                name="verified_email_has_reviewer",
            ),
            models.CheckConstraint(
                condition=~Q(status="rejected") | ~Q(rejection_reason=""),
                name="rejected_email_has_reason",
            ),
            models.UniqueConstraint(
                fields=["source_document"],
                condition=Q(status="verified"),
                name="one_verified_email_per_pdf",
            ),
        ]

    def clean(self):
        if bool(self.signing_user_id) == bool(self.customer_approver_id):
            raise ValidationError("Choose the account holder OR one named approver.")
        if not self.source_document_id:
            return
        pdf = self.source_document
        if pdf.quotation_id or pdf.invoice_id or pdf.internal_purchase_report_id:
            raise ValidationError(
                "Email signatures are only for professional inspections and completion cards."
            )
        source = pdf.inspection_report or pdf.completion_card
        if not source:
            raise ValidationError("Missing source inspection/completion document.")
        if pdf.inspection_report_id and pdf.inspection_report.purpose != "customer":
            raise ValidationError(
                "Internal reinspection cannot be sent for customer approval."
            )
        if pdf.status != "ready":
            raise ValidationError("Source PDF must have been generated successfully.")
        company = source.job_equipment.job.company
        if self.signing_user_id and self.signing_user_id != company.account_holder_id:
            raise ValidationError(
                {"signing_user": "User must be this company account holder."}
            )
        if self.customer_approver_id:
            require_match(
                company.pk,
                self.customer_approver.company_id,
                "customer_approver",
                "Approver belongs to another company.",
            )
            if self._state.adding and not self.customer_approver.is_active:
                raise ValidationError("Approver is inactive.")
        if self.status == "verified" and self.verified_by_id:
            verifier = self.verified_by
            if not verifier.is_active or not verifier.is_staff:
                raise ValidationError("Verifier must be an active staff user.")
        # The verify service additionally checks Sales Executive permission,
        # company/region scope, current signer authority and actual file contents.
