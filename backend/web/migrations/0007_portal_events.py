from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web","0006_customer_portal")]
    operations = [migrations.AlterField("notification", "event_type", models.CharField(max_length=30, choices=[
        ("assignment", "Technician assigned"),
        ("assignment_response", "Assignment accepted/declined"),
        ("quotation_sent", "Quotation sent"),
        ("quotation_decision", "Quotation accepted/rejected"),
        ("quotation_reminder", "Quotation reminder"),
        ("report_approval", "Report approval requested"),
        ("report_approved", "Report approved"),
        ("upcoming_service", "Upcoming service"),
        ("service_completed", "Service completed"),
        ("invoice_issued", "Invoice issued"),
        ("payment_overdue", "Payment overdue"),
        ("payment_recorded", "Payment recorded"),
        ("credit_issued", "Credit note issued"),
        ("amc_summary", "AMC monthly summary"),
        ("reschedule_decision", "Reschedule decision"),
    ]))]
