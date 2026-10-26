from django.core.management.base import BaseCommand

from apps.payroll.alerts import send_due_payslips


class Command(BaseCommand):
    help = (
        "Email each employee that their payslip is ready, for every approved pay run whose pay date has come. "
        "Run on Thursdays (hourly is fine: each email is queued once). A run that has not been approved by its "
        "pay date sends nothing and Owner and HR are told."
    )

    def handle(self, *args, **options):
        queued = send_due_payslips()
        self.stdout.write(f"Queued {queued} payslip email(s)." if queued else "No payslips to send.")
