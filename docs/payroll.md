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
   (NI number, tax code, date of birth, account name, sort code, account number, and where they apply
   `student_loan_plan`, `postgrad_loan`, `pension_opted_out`, `ni_category`, `apprentice_from`).
   A person starting mid-year on a cumulative tax code also needs `prior_pay` and `prior_tax` from their P45.
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

The system does **not** work out Statutory Sick Pay; it only stops an amount that is too high (more than
£123.25 for each week the sick days touch). When someone has approved sick leave in the week, their
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

## Tax, National Insurance, student loan and pension (suggested, then confirmed)

Every time a draft is built or rebuilt, each payslip that is not yet confirmed gets suggested figures, with
the reasons written on it (`deduction_notes`). `POST /api/payslips/<id>/suggest/` works them out again after
you change someone's pay details. Owner/HR must still check and confirm every payslip. The figures are in
`apps/payroll/taxrates.py`, one entry per tax year (only **2026/27** is entered). They were taken from payroll
publishers quoting HMRC, so **your accountant must check them before the first run**. A pay date in a year with
no entry blocks approval until the figures are added.

- **Income tax**: HMRC's PAYE specification (v24.0): free pay from the code, cumulative or Week 1/Month 1,
  band tests, 50% limit on tax taken in one payment, rounding down to the pound and the penny. Codes handled:
  `1257L`-style, `K`, `0T`, `BR`, `D0`, `D1`, `NT`, with `W1`/`M1`/`X` for non-cumulative. England, Wales
  and Northern Ireland bands. **Scottish (`S`) and Welsh (`C`) codes are not worked out**: the payslip says so
  and the figure is typed by hand. A refund is shown as nil with a note.
- **National Insurance** (employee): letters A, M, H, J, Z, C. Letter M is chosen automatically under 21, A
  otherwise; anyone who may be past State Pension age must have their letter set by hand.
- **Student loan**: Plans 1, 2, 4, 5 and postgraduate, 9% (6% postgraduate) above the weekly threshold (the
  annual figure divided by 52, rounded down to a pound), rounded down to a whole pound. Confirm this rounding
  with your accountant.
- **Pension** (auto-enrolment): 5% of qualifying earnings (£120 to £967 a week) for people aged 22 to 65
  earning over £192 a week, unless opted out. `PAYROLL_PENSION_TAX_METHOD` says whether the pension comes off
  before tax (`net_pay`, the default) or the provider claims the relief (`relief_at_source`): ask the pension
  provider which. The employer's 3% is not on the payslip.
- Rebuilding a draft replaces unconfirmed figures with new suggestions (a figure the system cannot work out is
  left as typed). Confirmed payslips are only reworked if their gross pay changes.

## Minimum wage

The hourly rate on every paid day (hours and holiday) must be at least the minimum for the person's age **on
that day** (so the check notices a 21st birthday part-way through a week): £12.71 from 21, £10.85 for 18 to 20,
£8.00 under 18, and £8.00 for an apprentice who is under 19 or in the first year of the apprenticeship
(`apprentice_from`). A rate below the minimum is refused when it is entered if the date of birth is known, and
**approval is blocked** if any day is under the minimum or if there is no date of birth to check against.

## Bank holidays

England bank holidays are marked on the hours table (`bank_holiday`). Pay is normal by default, because the
law does not require extra pay. If your contracts promise more, set `PAYROLL_BANK_HOLIDAY_MULTIPLIER` (for
example `1.5`); those hours are then a separate "Bank holiday pay" line. Annual leave booked on a bank holiday
is decided by the leave module, not here.

## Checks before a run can be approved

Every payslip with pay must have its deductions confirmed, net pay cannot be below zero, nobody may have
hours with no rate, nobody may be paid under the minimum wage, the tax figures for the year must exist, and
no hours may already have been paid in another run. Approving freezes the run and
the template wording and makes the PDFs.

## What is not covered yet

- **The tax figures are only as good as the 2026/27 table in `taxrates.py`.** GOV.UK itself could not be read
  when they were entered. Have the accountant check each number, and update the table every April.
- Statutory Sick Pay is not calculated (see above) and holiday pay is a day-based average (see above).
- Not worked out: Scottish and Welsh tax codes, NI letters other than A, M, H, J, Z, C, employer NI and employer
  pension on the payslip, attachment-of-earnings orders, and tax refunds (shown as nil with a note).
- Real-Time Information (RTI) reporting to HMRC is separate and is not done here. An accountant or payroll
  software must still file it.
- Attendance edited after a run is approved does not change that run. Correct it in the next week's pay.
- Payslips and bank details are personal data: only Owner and HR can see bank details (masked), and an
  employee can only see their own approved payslips.
