# Staff portal (website)

React + Vite, plain JavaScript. This first part has sign in, and for every account: the clock (in and out), your
next 7 days of shifts, annual leave (balance, request, cancel) and payslips (list and download).

Part 2 adds **Payroll** for Owner and HR (hidden from everyone else): the list of pay runs, starting one, and a pay-run
page to check each payslip (earnings, held hours, deductions), agree extra hours, enter statutory sick pay, confirm
tax and National Insurance, rebuild the draft, approve, then download the bank file and each payslip PDF. The
backend decides what is allowed; the page only shows its reasons in plain words.

Part 3 adds **Rota** for managers, HR and the owner (a manager only reaches their own shop) and **Swaps** for everyone:

- Rota: pick a shop and week; create the week (optionally copying last week); add, change and cancel shifts day by day,
  with saved patterns like Opening and Closing; open shifts are marked; hours per person; publish (with a confirmation).
  The backend checks every rule (overlaps, rest, hour limits, right to work, opening hours) and its reasons are shown
  as they come. Days that have passed cannot be changed.
- Swaps: staff ask a colleague to take a shift, the colleague accepts or declines, then the manager approves or
  rejects (a rejection needs a reason the staff see). Managers see what rule each swap would break before approving.

## Run it

```
cd frontend
npm install
npm run dev        # http://localhost:3000
npm test
npm run build      # static files in dist/
```

`npm run dev` expects the Django API on port 8000 and the clock service on 8001 (see the main README) and forwards
`/api/clock/...` to the clock service and the rest of `/api/` to Django, the same split the ingress makes in
Kubernetes. Use `HRMS_DJANGO` and `HRMS_CLOCK` to point elsewhere.

## How it works

- `src/api.js` is the only code that talks to the backend. The short-lived access token is kept in memory; the refresh
  token is kept in the browser's local storage so staff stay signed in on their phone, and is removed on sign out or
  when the server refuses it. An expired access token is renewed once, silently, and the request is retried.
- Errors from both backend services are turned into one plain sentence (`errorText`).
- Each panel on the home page loads on its own, so one failure never blanks the page.
- Owner and HR accounts that are not staff members have no rota, clock or payslips; they see a short explanation
  until the management screens exist.

## Not built yet

Pay rates, bank and tax details (they belong with the employee screens), the payslip template editor, attendance review, and employees, visas and leave approvals. Serving the
built files from Kubernetes (an image plus an ingress path for `/`) is also still to do.

## Notes from testing

- The login rate limit (10 a minute per address) also counts token refreshes, which happen whenever the site is opened.
  Many staff opening the site at once from one shop's internet connection could hit it; see the pull request notes.

- A server error never shows its raw page; the person sees "The server had a problem" instead.
- Payslips for people with no pay history get income tax £0.00 and a yellow "Check before you confirm" note from the
  backend (it needs the P45 figures or a Week 1/Month 1 code). The page makes that note hard to miss.

- Payslip PDFs downloaded here are **not** password protected; only the emailed copy is.
- The clock refuses a clock-out in the first minute of a shift and a clock-in when no shift is rostered; the page
  shows the server's message.
