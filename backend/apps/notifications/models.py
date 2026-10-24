import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class NotificationStatus(models.TextChoices):
    PENDING = "pending", "Waiting to be sent"
    RETRY = "retry", "Failed once, will try again"
    SENT = "sent", "Sent"
    FAILED = "failed", "Gave up"
    SKIPPED = "skipped", "Skipped (recipient inactive or has no email)"


class Notification(models.Model):
    """One email to one person. This table is the outbox.

    A row is written in the same database transaction as the thing it announces (a rota published,
    a swap approved...), so an email is queued if and only if the change really happened. Kafka
    then carries only the row's id; the consumer loads the row and sends the email. The table is
    the source of truth, so a Kafka outage delays emails but never loses them.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=40)
    context = models.JSONField(default=dict, help_text="Everything the email needs, written when it was queued.")
    dedupe_key = models.CharField(max_length=120, unique=True, help_text="Stops the same email being queued twice.")
    status = models.CharField(max_length=10, choices=NotificationStatus.choices, default=NotificationStatus.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True, help_text="When the id was last put on Kafka.")
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="notification_due_idx")]

    def __str__(self) -> str:
        return f"{self.kind} to {self.recipient_id} ({self.status})"
