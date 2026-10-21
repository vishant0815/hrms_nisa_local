from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.accounts.models import Role


class Store(models.Model):
    """One of the shops. Stores are deactivated, never deleted, so history stays intact."""

    name = models.CharField(max_length=100, unique=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)

    # Location used by attendance to flag clock-ins made away from the shop.
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    radius_m = models.PositiveIntegerField(
        default=100, validators=[MinValueValidator(10), MaxValueValidator(2000)],
        help_text="Clock-ins further than this from the shop are flagged for review.",
    )

    # The managers who run this store. A manager can see and edit only these stores.
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="managed_stores",
        limit_choices_to={"role": Role.MANAGER},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Weekday(models.IntegerChoices):
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class StoreHours(models.Model):
    """Opening hours for one weekday. Each shop can differ.

    If `closes` is earlier than `opens` the shop closes after midnight (e.g. 07:00-01:00).
    """

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="hours")
    weekday = models.IntegerField(choices=Weekday.choices)
    is_closed = models.BooleanField(default=False)
    opens = models.TimeField(null=True, blank=True)
    closes = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ["store", "weekday"]
        constraints = [
            models.UniqueConstraint(fields=["store", "weekday"], name="unique_store_weekday"),
        ]

    def __str__(self) -> str:
        return f"{self.store} {self.get_weekday_display()}"
