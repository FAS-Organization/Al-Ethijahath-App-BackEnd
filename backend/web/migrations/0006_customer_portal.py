from uuid import uuid4

from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
from web.models import private_upload_path


def set_regions(apps, schema_editor):
    db = schema_editor.connection.alias
    regions = apps.get_model("web", "Region").objects.using(db)
    companies = apps.get_model("web", "CustomerCompany").objects.using(db)
    sites = apps.get_model("web", "CustomerSite").objects.using(db)
    for name, code in [("Riyadh", "RYD"), ("Jeddah", "JED"), ("Dammam", "DMM")]:
        if (
            not regions.filter(name__iexact=name).exists()
            and not regions.filter(code__iexact=code).exists()
        ):
            regions.create(name=name, code=code, timezone="Asia/Riyadh", currency="SAR")
    for pk in companies.filter(region_id=None).values_list("pk", flat=True).iterator():
        ids = list(
            sites.filter(company_id=pk)
            .order_by()
            .values_list("region_id", flat=True)
            .distinct()[:2]
        )
        if len(ids) == 1:
            companies.filter(pk=pk).update(region_id=ids[0])


class Migration(migrations.Migration):
    dependencies = [("web", "0005_credit_notes")]
    operations = [
        migrations.AddField(
            model_name="user",
            name="password_changed_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="customercompany",
            name="region",
            field=models.ForeignKey(
                "web.Region", null=True, on_delete=models.PROTECT, related_name="companies"
            ),
        ),
        migrations.AddField(
            model_name="customersite",
            name="kind",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("branch", "Branch"),
                    ("warehouse", "Warehouse"),
                    ("sub_company", "Sub-company"),
                    ("other", "Other"),
                ],
                default="other",
            ),
        ),
        migrations.AddField(
            model_name="servicejob",
            name="request_photo",
            field=models.ImageField(upload_to=private_upload_path, blank=True),
        ),
        migrations.AddField(
            model_name="reschedulerequest",
            name="alternative_accepted_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.CreateModel(
            name="ServiceAccess",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.PROTECT,
                        related_name="service_access",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        "web.CustomerCompany", on_delete=models.PROTECT, related_name="delegates"
                    ),
                ),
                ("is_enabled", models.BooleanField(default=True)),
                ("token_version", models.PositiveIntegerField(default=0)),
            ],
            options={},
        ),
        migrations.CreateModel(
            name="SharingChallenge",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        "web.CustomerCompany", on_delete=models.PROTECT, related_name="+"
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        "web.ServiceAccess",
                        on_delete=models.PROTECT,
                        related_name="+",
                        null=True,
                        blank=True,
                    ),
                ),
                ("email", models.EmailField()),
                ("nonce", models.UUIDField(default=uuid4, editable=False, unique=True)),
                ("digest", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("verified_at", models.DateTimeField(null=True, blank=True)),
                ("consumed_at", models.DateTimeField(null=True, blank=True)),
            ],
            options={},
        ),
        migrations.CreateModel(
            name="ContactPreferences",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
                (
                    "company",
                    models.OneToOneField(
                        "web.CustomerCompany", on_delete=models.CASCADE, related_name="preferences"
                    ),
                ),
                ("email", models.BooleanField(default=True)),
                ("whatsapp", models.BooleanField(default=True)),
                ("sms", models.BooleanField(default=False)),
                ("quotations", models.BooleanField(default=True)),
                ("visits", models.BooleanField(default=True)),
                ("payments", models.BooleanField(default=True)),
            ],
            options={},
        ),
        migrations.CreateModel(
            name="RentalItem",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
                (
                    "region",
                    models.ForeignKey(
                        "web.Region", on_delete=models.PROTECT, related_name="rental_items"
                    ),
                ),
                ("code", models.SlugField()),
                ("name", models.CharField(max_length=150)),
                ("category", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True)),
                ("icon", models.CharField(max_length=32, blank=True)),
                ("available", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=["region", "code"], name="regional_rental_code")
                ],
            },
        ),
        migrations.CreateModel(
            name="RentalOrder",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        "web.CustomerCompany",
                        on_delete=models.PROTECT,
                        related_name="rental_orders",
                    ),
                ),
                (
                    "region",
                    models.ForeignKey("web.Region", on_delete=models.PROTECT, related_name="+"),
                ),
                (
                    "status",
                    models.CharField(
                        max_length=12,
                        choices=[
                            ("processing", "Pending"),
                            ("confirmed", "Confirmed"),
                            ("rejected", "Declined"),
                        ],
                        default="processing",
                    ),
                ),
                ("reason", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(null=True, blank=True)),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.PROTECT,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=~Q(status="rejected") | ~Q(reason=""),
                        name="declined_rental_reason",
                    ),
                    models.CheckConstraint(
                        condition=Q(status="processing", reviewed_at=None, reviewed_by=None)
                        | Q(
                            status__in=["confirmed", "rejected"],
                            reviewed_at__isnull=False,
                            reviewed_by__isnull=False,
                        ),
                        name="rental_review_actor",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="RentalOrderLine",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
                (
                    "order",
                    models.ForeignKey(
                        "web.RentalOrder", on_delete=models.PROTECT, related_name="items"
                    ),
                ),
                (
                    "item",
                    models.ForeignKey("web.RentalItem", on_delete=models.PROTECT, related_name="+"),
                ),
                ("name_snapshot", models.CharField(max_length=150)),
                ("quantity", models.PositiveIntegerField()),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["order", "item"], name="unique_rental_order_item"
                    ),
                    models.CheckConstraint(
                        condition=Q(quantity__gt=0), name="positive_rental_line"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="QuoteEmailRequest",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        "web.GeneratedDocument", on_delete=models.PROTECT, related_name="quote_mail"
                    ),
                ),
                ("recipient", models.EmailField()),
                ("sent_at", models.DateTimeField(null=True, blank=True)),
                ("last_attempt_at", models.DateTimeField(null=True, blank=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("cancelled_at", models.DateTimeField(null=True, blank=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["document", "recipient"], name="unique_quote_delivery"
                    )
                ],
                "permissions": [
                    ("verify_quotation_reply", "Verify a quotation acceptance received by email")
                ],
            },
        ),
        migrations.CreateModel(
            name="PendingServiceCharge",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
                (
                    "job_equipment",
                    models.ForeignKey(
                        "web.JobEquipment", on_delete=models.PROTECT, related_name="pending_charges"
                    ),
                ),
                ("amount", models.DecimalField(max_digits=16, decimal_places=2)),
                ("description", models.CharField(max_length=250)),
                (
                    "invoice_line",
                    models.OneToOneField(
                        "web.InvoiceLine", null=True, blank=True, on_delete=models.PROTECT
                    ),
                ),
                ("published_at", models.DateTimeField(null=True, blank=True)),
                ("settled_offline_at", models.DateTimeField(null=True, blank=True)),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=Q(amount__gt=0), name="pending_charge_positive"
                    ),
                    models.CheckConstraint(
                        condition=Q(invoice_line=None) | Q(settled_offline_at=None),
                        name="one_charge_settlement",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="AMCMonthlySummary",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="+",
                    ),
                ),
                (
                    "contract",
                    models.ForeignKey(
                        "web.AMCContract",
                        on_delete=models.PROTECT,
                        related_name="monthly_summaries",
                    ),
                ),
                ("month_start", models.DateField()),
                ("visits", models.JSONField(default=list, blank=True)),
                ("summary", models.TextField(blank=True)),
                ("file", models.FileField(upload_to=private_upload_path, blank=True)),
                ("published_at", models.DateTimeField(null=True, blank=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["contract", "month_start"], name="unique_contract_month"
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="documentapproval",
            name="quote_email",
            field=models.OneToOneField(
                "web.QuoteEmailRequest",
                on_delete=models.PROTECT,
                null=True,
                blank=True,
                related_name="decision",
            ),
        ),
        migrations.RemoveConstraint("documentapproval", "quote_approval_portal_customer"),
        migrations.AddConstraint(
            "documentapproval",
            models.CheckConstraint(
                condition=Q(quotation__isnull=True)
                | Q(method="in_app", signer_role="customer", signing_user__isnull=False)
                | Q(
                    method="quote_reply",
                    signer_role="customer",
                    signing_user__isnull=False,
                    quote_email__isnull=False,
                ),
                name="quote_decision_authority",
            ),
        ),
        migrations.AlterField(
            "documentapproval",
            "method",
            models.CharField(
                max_length=20,
                choices=[
                    ("offline", "Legacy offline approval"),
                    ("in_app", "Authenticated portal decision"),
                    ("on_site", "On-screen signature"),
                    ("email_link", "Legacy secure email link"),
                    ("email_attachment", "Verified returned signed PDF"),
                    ("quote_reply", "Quotation reply verified by Sales"),
                ],
            ),
        ),
        migrations.RunPython(set_regions, migrations.RunPython.noop),
    ]
