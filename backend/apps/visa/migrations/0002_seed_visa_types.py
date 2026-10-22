"""Starting set of UK statuses.

These are a starting point only. Home Office rules change and HR can edit every value in the
visa types table (or add new types). Check them against current guidance before relying on them.
"""
from django.db import migrations

# name, can_work, requires_expiry, max_weekly_hours, requires_sponsorship, requires_manual_decision, notes
VISA_TYPES = [
    ("British citizen", True, False, None, False, False, ""),
    ("Irish citizen", True, False, None, False, False, ""),
    ("Settled status (ILR / EU Settled Status)", True, False, None, False, False,
     "Indefinite. Any document expiry date does not end the right to work."),
    ("Pre-settled status", True, True, None, False, False, "Time-limited. Re-check before it expires."),
    ("Skilled Worker", True, True, None, True, False,
     "Only for the sponsoring employer, in the sponsored role."),
    ("Student", True, True, 20, False, False,
     "Weekly cap applies in term time (20 at degree level). Add vacation periods to lift it."),
    ("Graduate", True, True, None, False, False, ""),
    ("Dependant / partner (conditions vary)", True, True, None, False, True,
     "HR must confirm per person whether work is allowed, and record any conditions."),
    ("Visitor / no right to work", False, False, None, False, False, "Cannot be rostered."),
]


def seed(apps, schema_editor):
    VisaType = apps.get_model("visa", "VisaType")
    for name, can_work, requires_expiry, hours, sponsorship, manual, notes in VISA_TYPES:
        VisaType.objects.get_or_create(
            name=name,
            defaults={
                "can_work": can_work,
                "requires_expiry": requires_expiry,
                "max_weekly_hours": hours,
                "requires_sponsorship": sponsorship,
                "requires_manual_decision": manual,
                "notes": notes,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("visa", "0001_initial")]
    # Rolling back leaves the rows alone: HR may have edited them or attached employees to them.
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
