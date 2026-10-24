from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.visa.alerts import send_digest


class Command(BaseCommand):
    help = (
        "Email Owner and HR a daily list of immigration statuses that are expiring and students with no "
        "valid term letter. Sends nothing when there is nothing to report. Run daily (a CronJob later)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=60, help="Look ahead this many days (default 60).")

    def handle(self, *args, **options):
        queued = send_digest(timezone.localdate(), options["days"])
        self.stdout.write(f"Queued {queued} digest email(s)." if queued else "Nothing needs attention today.")
