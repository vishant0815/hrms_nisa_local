# Payroll and payslips

Pay is worked out from the attendance records, so nobody compares the phone app, the fingerprint
device and the rota by hand. This system **does not calculate tax**. Your accountant (or payroll
software) supplies income tax, National Insurance, pension and student loan figures, which you type
in and confirm for each payslip. It also **does not move money**: it makes a CSV for your bank.

## The weekly cycle

| When | What happens |
|---|---|
| Monday | `python manage.py create_weekly_pay_run` builds the draft for last Monday to Sunday and emails Owner and HR. |
| Mon to Wed | Owner or HR clears held shifts, enters and confirms each person's deductions, and approves the run. |
| Thursday | `python manage.py send_payslips` emails each employee a summary with their payslip attached as a password-protected PDF. |

Run `send_payslips` every hour on Thursdays (a Kubernetes CronJob later). It is safe to run often:
each email is queued once. If the run is not approved by its pay date, nothing is sent and Owner and
HR are emailed instead; once it is approved, the next run of the job sends the payslips.

## How hours become pay

- Paid time = clock-in to clock-out (phone app or fingerprint, whichever made the record), minus break
  time beyond the allowance (30 minutes for shifts of 6 to 12 hours, 60 minutes over 12 hours).
- Pay = minutes x hourly rate / 60, rounded to the penny, per rate, then added up. The rate used is the
  one in force on the day worked.
- A record is paid in the week it **started**.

### Held, not paid yet (offered again in every later run, never paid twice)

- still clocked in, or waiting for a manager's decision
- no hourly rate for that day (this also blocks approval)
- clocked time more than 15 minutes over the rota (`PAYROLL_EXTRA_HOURS_TOLERANCE_MINUTES`) until a
  manager approves it with `POST /api/attendance/<id>/approve-extra-hours/`

Rejected records are never paid. Attendance from before the first pay run is treated as already paid.

## Setting up

1. Set `PAYROLL_ENCRYPTION_KEY` (see `.env.example`) and back it up. Without it, bank details cannot be read.
2. Edit the template at `/api/payslip-template/` (wording, colour) and upload the logo at
   `/api/payslip-template/logo/` (PNG or JPEG, 1 MB or less).
3. For each person: `POST /api/employees/<id>/pay-rates/` and `PUT /api/employees/<id>/pay-details/`
   (NI number, tax code, account name, sort code, account number).
4. Create the first run: `POST /api/pay-runs/` with `{"period_start": "<a Monday>"}`.

## Holiday pay

Approved **annual leave** is paid in the week its days fall in (and any approved late is paid in the next run),
once only. Each leave day is paid at an **average paid day**: the minutes paid per worked day over the last
52 weeks of payslips, x the hourly rate on that day. A half day pays half; if a manager charged fewer days
than were asked for, the pay follows the days charged. Until there is pay history (a new starter, or the first
weeks of the system) the person's **typical day length** is used, set with `typical_day_hours` in their pay
details. Without either, the leave is held and the run cannot be approved. Unpaid, other, pending, rejected and
cancelled leave is not paid.

This is a day-based average, not the exact "week's pay" calculation in the law for irregular hours. Check the
method with your accountant before relying on it.

## Sick pay

The system does **not** work out Statutory Sick Pay. When someone has approved sick leave in the week, their
payslip is created even with no hours, lists the sick days, and cannot be approved until Owner/HR enters the
SSP figure from the accountant (`PATCH /api/payslips/<id>/sick-pay/`, use 0 if none is due) and confirms the
deductions. Sick days are covered once.

## The password-protected PDF in the email

The PDF is attached and protected (AES-256) with a password made from the employee's **first name in lower case
followed by their date of birth as mmyydd** (month, last two digits of the year, day): Sam, born 3 March 2000,
gets `sam030003`. Spaces in the first name are dropped. The email explains the rule with a made-up example and
never contains the password. The password is worked out when the email is sent and is not stored.

- Set each person's `date_of_birth` in their pay details (stored encrypted, shown only as its year). With no
  date of birth or first name, **nothing is attached** and the email tells them to sign in instead.
- A name plus a birthday is easy to guess for anyone who knows the person, so this stops casual reading, not a
  determined person. Staff can always open the same payslip after signing in.
- Email is not a secure channel. The attachment contains NI number and pay details; keep that in mind.

## Checks before a run can be approved

Every payslip with pay must have its deductions confirmed, net pay cannot be below zero, nobody may have
hours with no rate, and no hours may already have been paid in another run. Approving freezes the run and
the template wording and makes the PDFs.

## What is not covered yet

- **Tax, National Insurance, pension and student loan are not calculated.** Take the figures from your accountant.
- **Statutory Sick Pay is not calculated** (see above) and holiday pay is a day-based average (see above).
- Bank holidays and other public holidays are not treated specially.
- **National Minimum Wage is not checked.** Rates are whatever you enter, so check them against the current
  rate for each person's age.
- Real-Time Information (RTI) reporting to HMRC is separate and is not done here. An accountant or payroll
  software must still file it.
- Attendance edited after a run is approved does not change that run. Correct it in the next week's pay.
- Payslips and bank details are personal data: only Owner and HR can see bank details (masked), and an
  employee can only see their own approved payslips.
