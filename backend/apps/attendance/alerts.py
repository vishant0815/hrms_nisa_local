from django.utils import timezone

from apps.notifications.api import notify, person_name, store_approvers


def review_needed(record, event):
    """Tell the store's managers a clock entry needs a decision. `event` is "in" or "out"."""
    from .serializers import describe_flags
    from .services import REVIEW_FLAGS, base_flag

    problems = [d["message"] for d in describe_flags([f for f in record.flags if base_flag(f) in REVIEW_FLAGS])]
    moment = record.clock_in_at if event == "in" else record.clock_out_at
    notify(
        "attendance_review", store_approvers(record.store),
        {
            "employee": person_name(record.employee), "store": record.store.name,
            "when": timezone.localtime(moment).strftime("%a %d %b %H:%M"), "problems": problems,
        },
        f"attendance:{record.pk}:{event}",
    )
