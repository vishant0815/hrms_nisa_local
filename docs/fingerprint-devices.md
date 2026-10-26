# Fingerprint clock devices

A fingerprint device at each shop is the official way to clock in, clock out and take a break.
The phone app stays as a backup for when a device is down; every app clock-in at a shop that has a
device is flagged for the manager.

## Choosing a device

Check every one of these before buying. The system talks to devices over a simple secured web
endpoint, so the device must be able to send to an address we choose.

- [ ] Can **push each punch to a server address and path we set** (HTTPS), not only to the maker's own cloud
- [ ] Can send a **custom header or token**, or sits behind a small forwarder that can
- [ ] Has **four status keys**: check-in, check-out, break-out, break-in (without them a break cannot be told from a clock-out)
- [ ] **Keeps punches while offline** and sends them later, with the punch time
- [ ] Fingerprints and templates **stay on the device** (we never store them) and can be **deleted per person**
- [ ] A good fingerprint sensor with **live-finger detection**; cheap ones can be fooled by a fake finger
- [ ] Admin menu **locked with a password**, so nobody can change its clock or users
- [ ] Time synced by **NTP** if possible

Devices from the ZKTeco family that support "push" to a custom server are the usual choice. The
device protocol itself could not be tested here (no device), so a short on-site test is needed:
register the device, enrol one person, and check the four punches arrive. If a model sends a different
format, a small forwarder can reshape it into the format below; the rules do not change.

## Setting one up

1. HR registers the device: `POST /api/clock-devices/` with the store, a name and the device's serial number.
   The reply contains a **secret, shown once**. Put it in the device (or forwarder). A lost secret is replaced with
   `POST /api/clock-devices/<id>/rotate-secret/`.
2. HR enrols each person: `POST /api/fingerprint-enrolments/` with the employee and where the **signed consent** is kept.
   The reply gives that person's **device number**. Create that user number on every device and take their fingerprint there.
3. The device sends punches to the clock service:

```
POST /api/clock/device/punch          (batches of up to 100: /api/clock/device/punches)
X-Device-Serial: ZK-0001
Authorization: Bearer <secret>
{"user_id": 1, "kind": "in", "time": "2026-11-02T09:01:00+00:00"}
```

`kind` is `in`, `out`, `break_start` or `break_end`. `time` must include its timezone. The device should
also call `/api/clock/device/heartbeat` (`{"time": "..."}`) every few minutes, so managers can see it is
online and whether its clock is right.

## How punches are treated

- The time recorded is **our clock** when the punch arrives. If a device held a punch offline and sends it
  more than 5 minutes late (up to 24 hours), the device's own time is used and the entry is flagged for a manager to confirm.
  A device time in the future, or over 24 hours old, is refused.
- Sending the same punch twice changes nothing. A second tap of the same kind within a minute is ignored.
- A punch must be from an enrolled person who is rostered to work. Anything refused is **kept in the punch log with the reason**
  (`GET /api/clock-punches/?outcome=rejected`), so a manager can add the time by hand if it was real.
- A punch on a device at a different shop from the shift is flagged.

## Breaks

| Shift length | Break allowed |
|---|---|
| under 6 hours | none (a break is flagged) |
| 6 hours up to and including 12 hours | 30 minutes |
| over 12 hours | 60 minutes |

Break time **up to the allowance is paid; anything beyond it is unpaid**. The system reports the minutes;
payroll itself is separate. Both lengths are checked: the **rota shift** decides what is allowed while the break is taken, and the
**hours actually worked** confirm it at clock-out. If they give different paid time, the entry is flagged and the manager
chooses which counts (`POST /api/attendance/<id>/break-basis/`). Until then the hours worked are used.

The break report (`GET /api/attendance/breaks/`) shows, per person and per shift, the break taken, paid, unpaid and unused,
how often someone went over, and how often a shift had no break. Managers see their own shops only.

## Privacy

Fingerprints are **biometric data, a special category under UK GDPR**. Before going live:

- Complete a **data protection impact assessment** and update the staff privacy notice
- Record each person's **consent** (the enrolment requires a note of where it is kept)
- Offer an **alternative** for anyone who does not want to use a fingerprint. Withdrawing consent
  (`POST /api/fingerprint-enrolments/<id>/withdraw/`) stops their punches being accepted; that person uses the app, and
  their template must also be **deleted from every device by hand**
- Fingerprint templates never reach this system. We store only the device number, the punch times and the break times
- Break and punch details are visible to managers (their own shops) and the Owner/HR only, not to other staff

This is a technical summary, not legal advice. Please have the above checked by someone qualified.
