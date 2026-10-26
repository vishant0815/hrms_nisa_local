# hrms_nisa_local

A web-based HR management system for a group of 8 off-licence shops under one owner (about 200 users).

> **Status:** early development. Scope is agreed; the build is happening module by module.

## Features

- **Stores and employees**: 8 stores with their own opening hours; employees can work across shops, and cover at another shop needs approval from **both** the home and destination managers
- **Visa and right to work (UK)**: visa records, right-to-work checks, expiry alerts, and weekly-hour limits for Student visa holders
- **Rota**: store managers build their own shop's rota; employees see only their own shifts; the owner sees all 8 shops; shift swaps need manager approval
- **Attendance**: clock-in/out with location, device and IP checks; out-of-location clock-ins are flagged for manager review; manual edits are audited
- **Fingerprint clocking and breaks**: a fingerprint device at each shop for clock-in/out and breaks (30 minutes over 6 hours, 60 over 12; time beyond the allowance is unpaid), with break reports for managers; see `docs/fingerprint-devices.md`
- **Leave**: apply and approve, linked to the rota
- **Email notifications**: rota changes, swaps, cover requests, visa expiry and flagged attendance

## Roles

| Role | Access |
|---|---|
| Owner | All 8 stores |
| HR/Admin | All stores, including visa records |
| Store Manager | Own store (plus approved cover staff) |
| Employee | Own shifts, attendance, leave and payslips only |

## Tech stack

Django + Django REST Framework, FastAPI, Kafka, PostgreSQL, Redis, Docker, Kubernetes. The frontend is a responsive website (no native app).

## Planned structure

```
backend/            Django + DRF (accounts, stores, employees, visa, rota, leave)
fastapi_service/    Attendance and fraud checks
kafka_consumers/    Email notifications and audit logging
frontend/           Website
k8s/                Kubernetes manifests
docker-compose.yml  Local development stack
```

## Build order

1. Docker Compose (PostgreSQL, Redis, Kafka)
2. Accounts and roles
3. Stores and employees
4. Visa / right to work
5. Rota builder
6. Attendance with location checks
7. Email consumer
8. Frontend, then Kubernetes and CI/CD

## Privacy and security

This system handles immigration status and location data, which is sensitive personal data under UK GDPR.

- Never commit real employee data, passport or visa numbers, or secrets
- Keep `.env` files out of git; use `.env.example` for placeholders
- Location is captured only at clock-in and clock-out, never continuously
