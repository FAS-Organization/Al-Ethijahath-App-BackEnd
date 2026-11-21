from decimal import Decimal

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web", "0004_alter_customersite_address")]
    operations = [
        migrations.CreateModel(
            name="CreditNote",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "invoice",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="credit_notes",
                        to="web.invoice",
                    ),
                ),
                ("number", models.CharField(max_length=80, unique=True)),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=16,
                        validators=[MinValueValidator(Decimal("0.01"))],
                    ),
                ),
                ("reason", models.CharField(max_length=500)),
                (
                    "issued_at",
                    models.DateTimeField(default=django.utils.timezone.now, editable=False),
                ),
            ],
            options={
                "ordering": ["issued_at", "pk"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(amount__gt=0), name="credit_note_positive_amount"
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(reason="") & ~models.Q(number=""),
                        name="credit_note_identified",
                    ),
                ],
            },
        )
    ]
