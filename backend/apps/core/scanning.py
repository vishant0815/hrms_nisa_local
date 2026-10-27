"""Virus scanning for uploaded files, through a ClamAV daemon (clamd).

Uploads are untrusted. Before a file is stored it is streamed to clamd, which answers OK or names what it found.
If the scanner cannot be reached the upload is REFUSED (fail closed): a file nobody checked is never stored
in production. Scanning is switched off only when UPLOAD_SCANNING=off, which is the default while DEBUG is on.

Settings: UPLOAD_SCANNING (clamd|off), CLAMAV_HOST, CLAMAV_PORT, CLAMAV_TIMEOUT.
"""
import logging
import socket
import struct

from django.conf import settings
from rest_framework.exceptions import APIException, ValidationError

log = logging.getLogger(__name__)
CHUNK = 64 * 1024


class ScannerUnavailable(APIException):
    status_code = 503
    default_detail = "Files cannot be checked for viruses right now, so the upload was not accepted. Please try again shortly."
    default_code = "scanner_unavailable"


def enabled():
    return getattr(settings, "UPLOAD_SCANNING", "off") == "clamd"


def _ask(data: bytes):
    host, port, timeout = settings.CLAMAV_HOST, settings.CLAMAV_PORT, settings.CLAMAV_TIMEOUT
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(b"zINSTREAM\0")
        for start in range(0, len(data), CHUNK):
            chunk = data[start:start + CHUNK]
            sock.sendall(struct.pack("!I", len(chunk)) + chunk)
        sock.sendall(struct.pack("!I", 0))
        reply = b""
        while not reply.endswith(b"\0"):
            part = sock.recv(4096)
            if not part:
                break
            reply += part
    return reply.rstrip(b"\0").decode("utf-8", "replace").strip()


def scan(data: bytes, what="file"):
    """Return quietly for a clean file. Raise ValidationError for an infected one, ScannerUnavailable if unsure."""
    if not enabled():
        return
    try:
        answer = _ask(data)
    except (OSError, ValueError) as problem:  # refused, timed out, reset, bad host
        log.error("Virus scanner unreachable: %s", problem)
        raise ScannerUnavailable()
    if answer.endswith("OK"):
        return
    if answer.endswith("FOUND"):
        log.warning("Upload refused by the virus scanner: %s", answer)
        raise ValidationError({"file": f"This {what} was rejected because it may contain a virus."})
    log.error("Unexpected answer from the virus scanner: %r", answer)
    raise ScannerUnavailable()


def scan_upload(upload, what="file"):
    """Scan a Django UploadedFile without disturbing it for the caller."""
    if not enabled():
        return
    upload.seek(0)
    data = upload.read()
    upload.seek(0)
    scan(data, what)
