from django.db import migrations


def default_templates(apps, schema_editor):
    templates = apps.get_model("web", "DocumentTemplate").objects.using(schema_editor.connection.alias)
    for kind in ("inspection", "completion", "quotation", "invoice"):
        if not templates.filter(document_type=kind).exists():
            templates.create(document_type=kind, version=1, template_key=f"portal/{kind}/v1")


class Migration(migrations.Migration):
    dependencies = [("web", "0007_portal_events")]
    operations = [migrations.RunPython(default_templates, migrations.RunPython.noop)]
