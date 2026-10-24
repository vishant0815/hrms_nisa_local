from django.core.management.base import BaseCommand

from apps.notifications.bus import get_bus
from apps.notifications.delivery import deliver


class Command(BaseCommand):
    help = "Read queued emails from Kafka and send them. Runs forever, or until idle with --once."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Stop when there is nothing left to read.")

    def handle(self, *args, **options):
        get_bus().consume(deliver, once=options["once"])
