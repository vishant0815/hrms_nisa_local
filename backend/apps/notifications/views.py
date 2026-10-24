from django.db.models import Count, Min
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwnerOrHR

from .models import Notification, NotificationStatus


class NotificationStatusView(APIView):
    """For Owner and HR: is email flowing, and what could not be sent?"""

    permission_classes = [IsOwnerOrHR]

    def get(self, request):
        counts = dict(Notification.objects.values_list("status").annotate(n=Count("pk")))
        waiting = Notification.objects.filter(status__in=[NotificationStatus.PENDING, NotificationStatus.RETRY])
        oldest = waiting.aggregate(oldest=Min("created_at"))["oldest"]
        failures = Notification.objects.filter(status=NotificationStatus.FAILED).select_related("recipient")[:20]
        return Response({
            "counts": {status: counts.get(status, 0) for status in NotificationStatus.values},
            "oldest_waiting_minutes": int((timezone.now() - oldest).total_seconds() // 60) if oldest else None,
            "recent_failures": [
                {
                    "id": str(n.pk), "kind": n.kind, "recipient": n.recipient.email, "attempts": n.attempts,
                    "last_error": n.last_error, "created_at": n.created_at,
                }
                for n in failures
            ],
        })
