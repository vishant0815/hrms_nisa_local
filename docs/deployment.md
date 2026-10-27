# Deploying to Kubernetes

Manifests are in `k8s/`. They are written for a normal cluster with an nginx ingress controller. **They have been
checked against the Kubernetes 1.30 schemas but never applied to a real cluster**, so expect to adjust names,
storage and resource sizes on the first deploy.

## What runs

| Piece | What it is | Copies |
|---|---|---|
| `backend` | Django API (gunicorn), everything under `/api/` except the clock | 2 |
| `attendance-api` | Clock-in/out service (FastAPI), scales 2 to 6 on CPU | 2+ |
| `notification-relay`, `notification-consumer` | Put queued emails on Kafka, then send them (and attach payslip PDFs) | 1 each |
| `clamav` | Virus scanner. **Uploads are refused whenever it is not answering.** | 1 |
| CronJobs | Pay run (Monday 06:30), payslips (hourly on Thursday), visa checks, location clean-up, all in London time | |
| `hrms-migrate` Job | Database changes, run before each new version | |

PostgreSQL, Redis and Kafka are **not** in these files: use managed services or your cluster's usual operators, and
point `POSTGRES_HOST`, `REDIS_URL` and `KAFKA_BOOTSTRAP_SERVERS` in `k8s/config.yaml` at them.

## First deploy

1. Build and push the image from `backend/` (the same image runs every piece). Replace `hrms-backend:latest` in
   the YAML with your registry path.
2. Edit `k8s/config.yaml` (domain, database host, mail server) and `k8s/ingress.yaml` (domain, TLS secret).
3. Copy `k8s/secret.example.yaml` to `k8s/secret.yaml`, fill it in (it is git-ignored) and apply it. The
   `PAYROLL_ENCRYPTION_KEY` protects bank details and dates of birth: **back it up somewhere safe**.
4. In `k8s/storage.yaml` set a `storageClassName` that supports ReadWriteMany. The web app writes payslip PDFs
   and letters there and the email consumer reads them, so they must share one volume.
5. `kubectl apply -k k8s/`, then `kubectl apply -f k8s/jobs.yaml` and wait for the migrate job to finish
   (`kubectl -n hrms wait --for=condition=complete job/hrms-migrate`). For later releases, delete the old job first.
6. Create the first Owner account and set up payroll as described in `docs/payroll.md`.

## Things worth knowing

- **Routing**: `/api/clock/*` goes to `attendance-api`, the rest of `/api/` to `backend`. The website will be a
  third path on the same host when it exists.
- **Health checks** send a `Host` header, because Django rejects requests whose host it does not know. It must be
  one of `DJANGO_ALLOWED_HOSTS`.
- **Virus scanning** streams each upload to ClamAV before it is stored. ClamAV downloads its definitions on first
  start, which takes a few minutes. Outside DEBUG the app refuses to start unless `UPLOAD_SCANNING=clamd`. Locally
  `docker compose --profile scan up` starts a scanner.
- **HTTPS** ends at the ingress; Django trusts `X-Forwarded-Proto` and marks cookies secure when DEBUG is off.
- **Schedules** use `timeZone: Europe/London` (needs Kubernetes 1.27 or newer). `create_weekly_pay_run` stops with an
  error if an earlier draft is still unapproved, and the failed job shows in `kubectl get jobs`.
- Backups: back up PostgreSQL and the `private-media` volume together.

## What was tested

- The whole test suite (794 tests) passes on PostgreSQL 16.
- The image's entry point (gunicorn with production settings), migrations on PostgreSQL, the health check, and the
  Monday and Thursday commands were run outside a container.
- The ClamAV client is tested against a stand-in that speaks the real clamd protocol; **no real ClamAV was run**
  (it could not be installed in the test environment).
- Not done: a container build, an actual cluster, the ingress, the CronJob schedules firing, Kafka in a cluster.
