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
| Thursday | `python manage.py send_payslips` emails each employee a summary with a sign-in link. The PDF is not attached. |

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

## Checks before a run can be approved

Every payslip with pay must have its deductions confirmed, net pay cannot be below zero, nobody may have
hours with no rate, and no hours may already have been paid in another run. Approving freezes the run and
the template wording and makes the PDFs.

## What is not covered yet

- **Tax, National Insurance, pension and student loan are not calculated.** Take the figures from your accountant.
- **Holiday pay and sick pay are not added.** Approved leave does not create pay lines, so gross pay covers
  hours worked only. Agree with your accountant how holiday pay is handled; this is the next thing to build.
- **National Minimum Wage is not checked.** Rates are whatever you enter, so check them against the current
  rate for each person's age.
- Real-Time Information (RTI) reporting to HMRC is separate and is not done here. An accountant or payroll
  software must still file it.
- Attendance edited after a run is approved does not change that run. Correct it in the next week's pay.
- Payslips and bank details are personal data: only Owner and HR can see bank details (masked), and an
  employee can only see their own approved payslips.
