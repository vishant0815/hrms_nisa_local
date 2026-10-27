# Staff portal (website)

React + Vite, plain JavaScript. This first part has sign in, and for every account: the clock (in and out), your
next 7 days of shifts, annual leave (balance, request, cancel) and payslips (list and download).

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

Screens for Owner/HR payroll, manager rota and attendance, and employees, visas and leave approvals. Serving the
built files from Kubernetes (an image plus an ingress path for `/`) is also still to do.

## Notes from testing

- Payslip PDFs downloaded here are **not** password protected; only the emailed copy is.
- The clock refuses a clock-out in the first minute of a shift and a clock-in when no shift is rostered; the page
  shows the server's message.
