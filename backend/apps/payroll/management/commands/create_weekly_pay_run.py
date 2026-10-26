from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import ValidationError

from apps.payroll.alerts import create_weekly_run


class Command(BaseCommand):
    help = (
        "Build the draft pay run for last Monday to Sunday and email Owner and HR to check it. "
        "Run every Monday morning (a Kubernetes CronJob later). Does nothing if the run already exists."
    )

    def handle(self, *args, **options):
        try:
            run = create_weekly_run()
        except ValidationError as error:
            raise CommandError(f"Could not build last week's pay run: {error.detail}")
        if run is None:
            self.stdout.write("The pay run for last week already exists.")
        else:
            self.stdout.write(f"Draft pay run for {run.period_start} to {run.period_end}: {run.payslips.count()} payslip(s).")
