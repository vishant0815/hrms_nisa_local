from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.visa.services import expiring_visas


class Command(BaseCommand):
    help = (
        "List current immigration statuses that expire within N days or have already expired. "
        "Meant to run daily (a Kubernetes CronJob later). Email/Kafka alerts are added with "
        "the notifications module; for now this prints a report."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90, help="Look ahead this many days (default 90).")

    def handle(self, *args, **options):
        today = timezone.localdate()
        visas = list(expiring_visas(options["days"], today))
        if not visas:
            self.stdout.write(f"No statuses expire within {options['days']} days.")
            return
        for visa in visas:
            days_left = (visa.expiry_date - today).days
            when = f"EXPIRED {-days_left} day(s) ago" if days_left < 0 else f"expires in {days_left} day(s)"
            self.stdout.write(
                f"{visa.employee.employee_number}  {visa.employee.user.email}  "
                f"{visa.visa_type.name}  {visa.expiry_date:%d %b %Y}  {when}"
            )
        self.stdout.write(f"{len(visas)} status(es) need attention.")
