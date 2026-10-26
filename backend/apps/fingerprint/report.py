"""Break report: who takes how much break, and how much of it is unpaid."""
from collections import defaultdict

from . import breaks


def row(record, now):
    info = breaks.summary(record, now)
    return {
        "record": record.pk, "employee": record.employee, "store": record.store,
        "date": record.clock_in_at, "clock_in_at": record.clock_in_at, "clock_out_at": record.clock_out_at,
        "break_basis": record.break_basis or "actual", **info,
    }


def build(records, now):
    """(rows, per-person totals). Pass finished records only: open ones have no final break figures yet."""
    rows = [row(record, now) for record in records]
    people = defaultdict(lambda: {
        "shifts": 0, "taken": 0, "paid": 0, "unpaid": 0, "unused": 0, "over_count": 0, "no_break_count": 0, "longest": 0,
    })
    names = {}
    for item in rows:
        who = item["employee"].pk
        names[who] = item["employee"]
        total = people[who]
        total["shifts"] += 1
        for key in ("taken", "paid", "unpaid", "unused"):
            total[key] += item[key]
        total["over_count"] += item["unpaid"] > 0
        total["no_break_count"] += item["allowed"] > 0 and item["taken"] == 0
        total["longest"] = max(total["longest"], item["taken"])
    summary = []
    for who, total in people.items():
        summary.append({
            "employee": names[who], **total,
            "average_taken": round(total["taken"] / total["shifts"], 1),
        })
    summary.sort(key=lambda s: (-s["unpaid"], -s["taken"], s["employee"].employee_number or ""))
    return rows, summary
