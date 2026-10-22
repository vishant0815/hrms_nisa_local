from django.db import migrations


def mark_students(apps, schema_editor):
    VisaType = apps.get_model("visa", "VisaType")
    VisaType.objects.filter(name__icontains="student").update(requires_term_letter=True)


def unmark_students(apps, schema_editor):
    VisaType = apps.get_model("visa", "VisaType")
    VisaType.objects.filter(name__icontains="student").update(requires_term_letter=False)


class Migration(migrations.Migration):
    dependencies = [("visa", "0003_term_letters")]
    operations = [migrations.RunPython(mark_students, unmark_students)]
