from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.rota import services as rota_services
from apps.rota.tests import RotaTestBase, day

SHOP = (Decimal("51.500000"), Decimal("-0.120000"))
NEAR = (51.50045, -0.12)  # about 50 m away
FAR = (51.5045, -0.12)  # about 500 m away


def at(n, clock):
    """An aware datetime on rota day n at the given 'HH:MM' (shop time)."""
    return timezone.make_aware(datetime.combine(day(n), time.fromisoformat(clock)))


def make_shift(test, employee, n, start, end, week=None):
    return rota_services.create_shift(
        rota_week=week or test.week_a, by=test.hr, employee=employee, date=day(n),
        start_time=time.fromisoformat(start), end_time=time.fromisoformat(end),
    )[0]


def attendance_fixture(test):
    """Shop A has a location; emp_a is rostered 09:00-17:00 on day 0 on a published rota."""
    RotaTestBase.setUp(test)
    test.store_a.latitude, test.store_a.longitude, test.store_a.radius_m = SHOP[0], SHOP[1], 100
    test.store_a.save()
    test.shift_a = make_shift(test, test.emp_a, 0, "09:00", "17:00")
    rota_services.publish_week(rota_week=test.week_a, by=test.hr)


class AttendanceBase(RotaTestBase):
    def setUp(self):
        attendance_fixture(self)

    def shift(self, employee, n, start, end, week=None):
        return make_shift(self, employee, n, start, end, week)

    def clock_in(self, when="09:00", where=NEAR, accuracy=10, device="", employee=None, n=0):
        from . import services

        return services.clock_in(
            employee=employee or self.emp_a, latitude=where[0] if where else None,
            longitude=where[1] if where else None, accuracy_m=accuracy, device_id=device, now=at(n, when),
        )

    def clock_out(self, when="17:00", where=NEAR, accuracy=10, device="", employee=None, n=0):
        from . import services

        return services.clock_out(
            employee=employee or self.emp_a, latitude=where[0] if where else None,
            longitude=where[1] if where else None, accuracy_m=accuracy, device_id=device, now=at(n, when),
        )
