"""Small helpers shared by the test suites."""
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import Role, User

STRONG_PASSWORD = "Correct-Horse-9-Battery"


def make_user(email, role=Role.EMPLOYEE, **extra):
    return User.objects.create_user(email=email, password=STRONG_PASSWORD, role=role, **extra)


def days_from_today(days):
    return timezone.localdate() + timedelta(days=days)
