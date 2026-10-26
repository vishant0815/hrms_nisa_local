"""Getting queued emails onto Kafka (relay) and out to people (deliver)."""
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage, send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .emails import RENDERERS, clean_subject, load_attachment_builder
from .models import Notification, NotificationStatus

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
VISIBILITY_TIMEOUT = timedelta(minutes=10)  # republish an id nobody has acted on for this long
OPEN = (NotificationStatus.PENDING, NotificationStatus.RETRY)


def relay_due(bus, now=None, limit=200):
    """Publish ids for emails that are due and not already on the bus. Returns how many."""
    now = now or timezone.now()
    due = (
        Notification.objects.filter(status__in=OPEN, next_attempt_at__lte=now)
        .filter(Q(published_at__isnull=True) | Q(published_at__lt=now - VISIBILITY_TIMEOUT))
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )
    ids = [str(pk) for pk in due]
    if not ids:
        return 0
    confirmed = bus.publish(ids)
    Notification.objects.filter(pk__in=confirmed).update(published_at=now)
    return len(confirmed)


def deliver(notification_id, now=None):
    """Send one email. Safe to call again for the same id: a finished email is never sent twice."""
    now = now or timezone.now()
    try:
        pk = uuid.UUID(str(notification_id))
    except ValueError:
        return None
    with transaction.atomic():
        item = Notification.objects.select_for_update().select_related("recipient").filter(pk=pk).first()
        if item is None or item.status not in OPEN:
            return item
        if not item.recipient.is_active or not item.recipient.email:
            item.status = NotificationStatus.SKIPPED
            item.save(update_fields=["status"])
            return item
        renderer = RENDERERS.get(item.kind)
        if renderer is None:
            item.status, item.last_error = NotificationStatus.FAILED, f"Unknown kind: {item.kind}"[:300]
            item.save(update_fields=["status", "last_error"])
            return item
        try:
            subject, body = renderer(item.context, item.recipient)
            builder = load_attachment_builder(item.kind)
            files = builder(item.context, item.recipient) if builder else []
            if files:
                message = EmailMessage(clean_subject(subject), body, settings.DEFAULT_FROM_EMAIL, [item.recipient.email])
                for filename, content, mimetype in files:
                    message.attach(filename, content, mimetype)
                message.send(fail_silently=False)
            else:
                send_mail(clean_subject(subject), body, settings.DEFAULT_FROM_EMAIL, [item.recipient.email])
        except Exception as error:  # a mail server problem must not lose the email
            log.warning("Email %s failed: %s", item.pk, type(error).__name__)
            item.attempts += 1
            item.last_error = f"{type(error).__name__}: {error}"[:300]
            if item.attempts >= MAX_ATTEMPTS:
                item.status = NotificationStatus.FAILED
            else:
                item.status = NotificationStatus.RETRY
                item.next_attempt_at = now + timedelta(minutes=2 ** item.attempts)
                item.published_at = None
            item.save()
            return item
        item.status, item.sent_at, item.last_error = NotificationStatus.SENT, now, ""
        item.save(update_fields=["status", "sent_at", "last_error"])
        return item
