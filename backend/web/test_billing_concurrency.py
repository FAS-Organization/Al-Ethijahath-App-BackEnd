from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from web import models as m


@skipUnless(connection.vendor == "postgresql", "Tests PostgreSQL row locking")
class LedgerConcurrencyTests(TransactionTestCase):
    def test_payment_and_credit_cannot_both_spend_the_same_balance(self):
        region, _ = m.Region.objects.get_or_create(
            code="TEST",
            defaults={"name": "Test region", "currency": "SAR", "timezone": "Asia/Riyadh"},
        )
        owner = m.User.objects.create_user(
            "payer@example.com", is_active=True, account_status="active"
        )
        company = m.CustomerCompany.objects.create(
            name="Customer",
            contact_name="Owner",
            contact_email=owner.email,
            account_holder=owner,
            region=region,
        )
        site = m.CustomerSite.objects.create(company=company, region=region, name="Test site")
        job = m.ServiceJob.objects.create(
            company=company, site=site, region=region, job_type="service", description="Repair"
        )
        invoice = m.Invoice.objects.create(
            company=company,
            job=job,
            region=region,
            number="INV-C",
            status="issued",
            currency="SAR",
            issue_date=timezone.localdate(),
            due_date=timezone.localdate(),
            issued_at=timezone.now(),
            subtotal=100,
            total=100,
        )
        gate = Barrier(2)

        def post(kind):
            try:
                gate.wait(timeout=8)
                if kind == "credit":
                    m.CreditNote.objects.create(
                        invoice_id=invoice.pk,
                        created_by_id=owner.pk,
                        number="CN-C",
                        amount=Decimal("70.00"),
                        reason="Rate correction",
                    )
                else:
                    m.Payment.objects.create(
                        invoice_id=invoice.pk,
                        amount=Decimal("70.00"),
                        method="cash",
                        paid_at=timezone.now(),
                    )
                return True
            except ValidationError:
                return False
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(post, ["credit", "payment"]), [True, False])
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, Decimal("30.00"))
