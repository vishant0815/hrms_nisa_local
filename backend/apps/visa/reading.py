"""Read suggested dates out of a term-dates letter.

This is a helper for HR, never a decision. It only works on PDFs that contain real text (not
photos or scans), it can be wrong, and nothing it finds is used until HR confirms the dates.
Any failure just means "no suggestion": uploading must never break because of it.
"""
import io
import logging
import re
from datetime import date

from django.utils import timezone

logging.getLogger("pypdf").setLevel(logging.ERROR)  # broken uploads are expected; don't flood the logs

MAX_PAGES = 10
MAX_CHARS = 30000

MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sept", "sep"), ("october", "oct"), ("november", "nov"), ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_MONTH_WORDS = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_PATTERNS = [
    # 1 October 2026, 1st Oct 2026
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_WORDS})\.?,?\s+(\d{{4}})\b", re.I),
    # October 1, 2026
    re.compile(rf"\b({_MONTH_WORDS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),
    # 01/10/2026 (UK day first)
    re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b"),
    # 2026-10-01
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
]
VACATION_WORDS = re.compile(r"vacation|holiday|break|christmas|easter|summer|reading week|recess", re.I)
IGNORED_LINES = re.compile(r"^\s*(date|dated|issued|letter date|printed)\b", re.I)


def extract_text(upload, content_type):
    """Plain text from a PDF, or "" if the file is an image, scanned, encrypted or unreadable."""
    if content_type != "application/pdf":
        return ""
    try:
        from pypdf import PdfReader

        upload.seek(0)
        reader = PdfReader(io.BytesIO(upload.read()))
        if reader.is_encrypted:
            return ""
        parts = []
        for page in reader.pages[:MAX_PAGES]:
            parts.append(page.extract_text() or "")
            if sum(map(len, parts)) > MAX_CHARS:
                break
        return "\n".join(parts)[:MAX_CHARS]
    except Exception:  # a hostile or broken PDF must never break the upload
        return ""
    finally:
        try:
            upload.seek(0)
        except Exception:
            pass


def _to_date(kind, groups):
    try:
        if kind == 0:
            return date(int(groups[2]), MONTHS[groups[1].lower()], int(groups[0]))
        if kind == 1:
            return date(int(groups[2]), MONTHS[groups[0].lower()], int(groups[1]))
        if kind == 2:
            return date(int(groups[2]), int(groups[1]), int(groups[0]))
        return date(int(groups[0]), int(groups[1]), int(groups[2]))
    except (ValueError, KeyError):
        return None


def dates_in(line, today=None):
    """Sensible dates found in a line, in the order they appear."""
    today = today or timezone.localdate()
    found = []
    for kind, pattern in enumerate(DATE_PATTERNS):
        for match in pattern.finditer(line):
            parsed = _to_date(kind, match.groups())
            if parsed and abs(parsed.year - today.year) <= 5:
                found.append((match.start(), parsed))
    return [d for _, d in sorted(found)]


def suggest_dates(text, today=None):
    """Guess the term dates and vacation periods. Returns {} when nothing useful was found."""
    term_dates, vacations = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or IGNORED_LINES.match(line):
            continue
        found = dates_in(line, today)
        if not found:
            continue
        if VACATION_WORDS.search(line) and len(found) >= 2:
            start, end = min(found[:2]), max(found[:2])
            label = re.sub(r"\s+", " ", re.split(r"[:\-–]|\d", line, maxsplit=1)[0]).strip()[:100]
            vacations.append({"start_date": start.isoformat(), "end_date": end.isoformat(), "label": label})
        else:
            term_dates.extend(found)
    if not term_dates:
        return {}
    covers_from, covers_to = min(term_dates), max(term_dates)
    inside = [
        v for v in vacations
        if covers_from.isoformat() <= v["start_date"] and v["end_date"] <= covers_to.isoformat()
    ]
    return {
        "covers_from": covers_from.isoformat(),
        "covers_to": covers_to.isoformat(),
        "vacations": inside,
        "note": "Read automatically from the file. Check it against the letter before confirming.",
    }


def suggestions_for(upload, content_type):
    try:
        return suggest_dates(extract_text(upload, content_type))
    except Exception:
        return {}
