import logging
import time

from django.core.management.base import BaseCommand

from apps.notifications.bus import get_bus
from apps.notifications.delivery import relay_due

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Put queued emails on Kafka. Runs forever, or once with --once."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Publish what is due, then stop.")
        parser.add_argument("--interval", type=float, default=5.0, help="Seconds between checks (default 5).")

    def handle(self, *args, **options):
        bus = get_bus()
        while True:
            try:
                count = relay_due(bus)
                if count:
                    self.stdout.write(f"Published {count} notification(s).")
            except Exception:  # Kafka being down must not stop the relay for good
                log.exception("Could not publish notifications; will try again")
            if options["once"]:
                return
            time.sleep(options["interval"])
