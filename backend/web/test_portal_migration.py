from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RegionalUpgradeTests(TransactionTestCase):
    def test_existing_accounts_are_preserved_and_ambiguous_regions_require_review(self):
        start = [("web", "0004_alter_customersite_address")]
        latest = [("web", "0008_publication_templates")]
        executor = MigrationExecutor(connection)
        executor.migrate(start)
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        apps = executor.loader.project_state(start).apps
        Region = apps.get_model("web", "Region")
        User = apps.get_model("web", "User")
        Company = apps.get_model("web", "CustomerCompany")
        Site = apps.get_model("web", "CustomerSite")
        old_regions = [
            Region.objects.get_or_create(
                code=code, defaults={"name": name, "timezone": "Asia/Riyadh", "currency": "SAR"}
            )[0]
            for name, code in [("Riyadh", "RYD"), ("Jeddah", "JED")]
        ]
        accounts = []
        for i, region_list in enumerate([[old_regions[0]], [old_regions[1]], old_regions, []]):
            owner = User.objects.create(email=f"legacy{i}@example.com")
            company = Company.objects.create(
                account_holder=owner,
                name="Same company name",
                contact_name="Owner",
                contact_email=owner.email,
            )
            for region in region_list:
                Site.objects.create(
                    company=company, region=region, name="Branch", address="Warehouse"
                )
            accounts.append(company.pk)
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        Company = executor.loader.project_state(latest).apps.get_model("web", "CustomerCompany")
        self.assertEqual(
            [Company.objects.get(pk=k).region_id for k in accounts],
            [old_regions[0].pk, old_regions[1].pk, None, None],
        )
        self.assertEqual(Company.objects.filter(pk__in=accounts).count(), 4)
