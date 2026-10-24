from django.core.management.base import BaseCommand

from apps.attendance import services


class Command(BaseCommand):
    help = "Clear raw GPS coordinates from attendance records older than the retention period."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90, help="Keep coordinates this many days (default 90).")

    def handle(self, *args, **options):
        if options["days"] < 1:
            self.stderr.write("--days must be at least 1.")
            return
        count = services.purge_locations(options["days"])
        self.stdout.write(f"Cleared coordinates on {count} attendance record(s) older than {options['days']} days.")
