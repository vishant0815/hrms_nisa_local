"""Clock-in / clock-out service (FastAPI).

Why a separate service: every employee taps this at shift start, so it is the busiest part of the
system and can be scaled on its own. It holds no business rules of its own. It checks the same
login token as the main API, then calls the shared rules in apps.attendance.services, so the
Django API and this service can never disagree.

Run:  uvicorn attendance_api.main:app --host 0.0.0.0 --port 8001
Route /api/clock/* to this service and everything else under /api/ to Django (see docker-compose).

The time of a clock event is always the server's. Anything a phone says about time is ignored.
"""
import os
from datetime import datetime
from functools import wraps
from typing import Literal, Optional

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import close_old_connections  # noqa: E402
from fastapi import Depends, FastAPI, Header, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # noqa: E402
from pydantic import BaseModel, Field, field_validator, model_validator  # noqa: E402
from rest_framework_simplejwt.exceptions import TokenError  # noqa: E402
from rest_framework_simplejwt.tokens import AccessToken  # noqa: E402

from apps.accounts.models import User  # noqa: E402
from apps.attendance import services  # noqa: E402
from apps.attendance.models import ReviewStatus  # noqa: E402
from apps.employees.models import EmployeeProfile  # noqa: E402
from apps.fingerprint import services as fingerprint  # noqa: E402

app = FastAPI(title="HRMS clock service", docs_url=None, redoc_url=None, openapi_url=None)
bearer = HTTPBearer(auto_error=False)


def tidy(handler):
    """Give the database connection back when the request is done.

    Requests run in a pool of worker threads and each thread keeps its own Django connection. Without this a
    connection stays open until that thread happens to serve another request.
    """

    @wraps(handler)
    def inner(*args, **kwargs):
        close_old_connections()
        try:
            return handler(*args, **kwargs)
        finally:
            close_old_connections()

    return inner


class Problem(Exception):
    def __init__(self, status_code, code, message):
        self.status_code, self.code, self.message = status_code, code, message


@app.exception_handler(Problem)
async def problem_handler(request: Request, exc: Problem):
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message})


@app.exception_handler(services.AttendanceError)
async def attendance_handler(request: Request, exc: services.AttendanceError):
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message})


def current_employee(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> EmployeeProfile:
    """The employee behind a valid access token from the main API. Runs in a worker thread."""
    close_old_connections()
    try:
        if credentials is None:
            raise Problem(401, "not_signed_in", "Please sign in.")
        try:
            token = AccessToken(credentials.credentials)  # checks signature, expiry and token type
            user = User.objects.get(pk=token["user_id"], is_active=True)
        except (TokenError, User.DoesNotExist, KeyError):
            raise Problem(401, "not_signed_in", "Please sign in again.")
        employee = EmployeeProfile.objects.select_related("user").filter(user=user).first()
        if employee is None:
            raise Problem(403, "no_employee_profile", "You do not have an employee profile.")
        return employee
    finally:
        close_old_connections()


class Location(BaseModel):
    """Where the phone says it is. All optional: refusing location is allowed, but gets flagged."""

    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    accuracy_m: Optional[float] = Field(default=None, ge=0, le=100_000)
    device_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_.:-]*$")

    @model_validator(mode="after")
    def both_or_neither(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Send latitude and longitude together.")
        return self

    def as_kwargs(self):
        return {
            "latitude": self.latitude, "longitude": self.longitude,
            "accuracy_m": None if self.accuracy_m is None else round(self.accuracy_m),
            "device_id": self.device_id,
        }


def result(record, message):
    return {
        "id": record.pk,
        "store": {"id": record.store_id, "name": record.store.name},
        "clock_in_at": record.clock_in_at,
        "clock_out_at": record.clock_out_at,
        "late_minutes": record.late_minutes,
        "needs_review": record.review_status == ReviewStatus.PENDING,
        "message": message + (" Your manager will check this entry." if record.review_status == ReviewStatus.PENDING else ""),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/clock/in", status_code=201)
@tidy
def clock_in(where: Location, employee: EmployeeProfile = Depends(current_employee)):
    record = services.clock_in(employee=employee, **where.as_kwargs())
    return result(record, "You are clocked in.")


@app.post("/clock/out")
@tidy
def clock_out(where: Location, employee: EmployeeProfile = Depends(current_employee)):
    record = services.clock_out(employee=employee, **where.as_kwargs())
    return result(record, "You are clocked out.")


@app.get("/clock/status")
@tidy
def clock_status(employee: EmployeeProfile = Depends(current_employee)):
    record = services.open_record(employee)
    if record is None:
        return {"clocked_in": False}
    return {"clocked_in": True, "since": record.clock_in_at, "store": {"id": record.store_id, "name": record.store.name}}


# --- Fingerprint devices ----------------------------------------------------------------------
# A device is not a person: it signs in with its serial number (X-Device-Serial) and its secret
# (Authorization: Bearer ...), both set up by HR. The time a punch is recorded at is decided by
# apps.fingerprint.services (our clock, unless the device sent it late).

def current_device(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    x_device_serial: str = Header(default="", max_length=64),
):
    close_old_connections()
    try:
        device = fingerprint.authenticate(x_device_serial, credentials.credentials if credentials else "")
        if device is None:
            raise Problem(401, "device_not_recognised", "This device is not recognised.")
        return device
    finally:
        close_old_connections()


class DevicePunch(BaseModel):
    user_id: int = Field(ge=0, le=2_000_000_000)
    kind: Literal["in", "out", "break_start", "break_end"]
    time: datetime

    @field_validator("time")
    @classmethod
    def must_have_a_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("Send the time with its timezone, for example 2026-11-02T09:01:00+00:00.")
        return value


class DevicePunchBatch(BaseModel):
    punches: list[DevicePunch] = Field(min_length=1, max_length=100)


class DeviceHeartbeat(BaseModel):
    time: datetime

    @field_validator("time")
    @classmethod
    def must_have_a_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("Send the time with its timezone.")
        return value


def punch_result(punch):
    return {"id": punch.pk, "outcome": punch.outcome, "reason": punch.reason}


@app.post("/clock/device/punch")
@tidy
def device_punch(body: DevicePunch, device=Depends(current_device)):
    punch = fingerprint.process_punch(device=device, user_id=body.user_id, kind=body.kind, device_time=body.time)
    return punch_result(punch)


@app.post("/clock/device/punches")
@tidy
def device_punches(body: DevicePunchBatch, device=Depends(current_device)):
    """Punches a device held while it was offline. Handled oldest first."""
    results = []
    for item in sorted(body.punches, key=lambda p: p.time):
        punch = fingerprint.process_punch(device=device, user_id=item.user_id, kind=item.kind, device_time=item.time)
        results.append(punch_result(punch))
    return {"results": results}


@app.post("/clock/device/heartbeat")
@tidy
def device_heartbeat(body: DeviceHeartbeat, device=Depends(current_device)):
    """Lets managers see the device is online and whether its clock is right."""
    fingerprint.note_contact(device, body.time, fingerprint.attendance.current_time())
    return {"status": "ok"}
