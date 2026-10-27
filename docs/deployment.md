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

The whole stack was deployed to a real single-node Kubernetes cluster (k3s 1.30 with ingress-nginx), using
`k8s-test/`: the production manifests plus a throw-away PostgreSQL, Kafka and mail catcher (MailHog), a ReadWriteOnce
volume, one replica each and plain HTTP on `hr.local`. In that cluster:

- The image builds, and every pod becomes Ready. The migrate job runs; the backend only becomes Ready after it.
- The ingress sends `/api/health/` to Django and `/api/clock/...` to the clock service (rewritten to `/clock/...`);
  unknown hosts get 404 and bodies over 6 MB get 413. **This found a bug**: the first version forwarded
  `/api/clock/in` unchanged to a service that serves `/clock/in`. The clock route is now its own Ingress.
- Real ClamAV in the cluster: a clean PDF is accepted, EICAR inside a compressed PDF is rejected, and with ClamAV
  stopped uploads get 503 and work again once it is back. The NetworkPolicy lets only the backend reach ClamAV
  (checked with it on and off). The `RealClamavTests` in the suite also pass against it.
- All five CronJobs were fired by hand and completed; the weekly job built a draft pay run and the visa jobs queued
  their emails.
- Email: outbox, relay, real Kafka, consumer and a real SMTP server. A payslip email arrived with an AES-256
  encrypted PDF that opens with the first-name-plus-birthday password, not with a wrong one, and the password is not
  in the message. The PDF was written by the web pod and read by the consumer pod through the shared volume.
- The minimum wage check blocked approving a pay run at £12.00/hour for a 26 year old, inside the cluster.
- Killing both backend pods at once gave 2 failed requests out of 30 before the replacements took over.
- The whole test suite (794 tests) passes on SQLite and PostgreSQL 16, and the production manifests pass a
  server-side dry run.

**Not tested** (be honest with yourself about these before going live):

- A ReadWriteMany volume. The test cluster only offers ReadWriteOnce, which works with several pods only because it
  is one node. Check your storage class really supports ReadWriteMany.
- More than one node, and the autoscaler (the test cluster has no metrics server).
- TLS and certificates, a real mail provider, a real Kafka cluster with more than one broker, and a managed PostgreSQL.
- The CronJobs firing on their own schedules (they were started by hand; the schedule text is valid).
- The test cluster needed sandbox-only workarounds (a container runtime setting and a newer runc) that you will not need.

To repeat the cluster test: build the image, load it into the cluster, install ingress-nginx, create a `hrms-secrets`
Secret with throw-away values, then `kubectl apply -k k8s-test` and the migrate job as above.
