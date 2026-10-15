# RecoverAI

[![CI](https://github.com/uddhav05-cyber/Recover.ai/actions/workflows/ci.yml/badge.svg)](https://github.com/uddhav05-cyber/Recover.ai/actions/workflows/ci.yml)

RecoverAI is an auditable FastAPI and React agent for bounded recovery of failed Razorpay subscription payments.

**What:** Diagnose failed payments, choose a policy-approved recovery action, and record the complete episode.
**Why:** Blind retry schedules waste attempts and give an LLM too much power over money-moving operations.
**Result:** A deterministic guardrail gates every action, with an audit record written before external payment calls.

![RecoverAI dashboard screenshot](docs/screenshots/dashboard.png)

## The problem

Failed subscription payments are usually handled with a fixed retry schedule regardless of why the payment failed. RecoverAI classifies the failure, applies an explicit policy, and routes only bounded, explainable actions toward execution.

## Architecture

```
flowchart LR
	R[Razorpay webhook] --> I[Signature validation + idempotent ingestion]
	I --> D[Diagnose: rules first]
	D --> P[Decide: explicit policy]
	P --> G[Guardrail: deterministic gate]
	G --> A[Act: retry or payment link]
	A --> W[Delayed retry worker]
	D --> DB[(Postgres audit trail)]
	G --> DB
	A --> DB
	DB --> V[React dashboard]
```

## Results (Simulated Data)

Seed `20260826`, 60 generated cases:

| Metric | RecoverAI | No-action baseline |
|---|---:|---:|
| Cases processed | 60 | 60 |
| Recovered | 14 (23.3%) | 0 (0.0%) |
| Amount recovered | Rs 10,886.00 | Rs 0.00 |
| Still at risk | Rs 37,354.00 | Rs 48,240.00 |
| Exceptions | 46 | 60 |

The batch uses a simulated Razorpay client. These numbers demonstrate the pipeline and its stopping rules, not real-world recovery rates.

Category breakdown will be reported by the batch runner as recovered and at-risk counts and amounts.

## Quick Start (Docker)

Requirements: Docker Desktop or Docker Engine with Compose.

From the repository root:

```bash
docker compose up --build
```

Open `http://localhost:8080`. The API is available at `http://localhost:8000`; migrations run when the backend container starts.

To run only Postgres and use local processes:

```bash
docker compose up -d postgres
cd backend
cp .env.example .env
sed -i 's#^DATABASE_URL=.*#DATABASE_URL=postgresql+asyncpg://recoverai:recoverai@localhost:5432/recoverai#' .env
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --reload
```

In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

## Manual Setup (Windows/macOS/Linux)

Requirements: Python 3.13, Node.js 20+, and a reachable Postgres database. Docker is not required.

### Windows PowerShell

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
# Edit .env and set DATABASE_URL to your Postgres URL.

alembic upgrade head
Start-Process powershell -ArgumentList '-NoExit', '-Command', 'cd frontend; npm ci; npm run dev'
uvicorn app.main:app --reload
```

### macOS/Linux

```bash
cd backend
python3.13 -m venv .venv
. .venv/bin/activate
cp .env.example .env
# Edit .env and set DATABASE_URL to your Postgres URL.
python -m pip install -e '.[dev]'
alembic upgrade head
cd ../frontend
npm ci
npm run dev
```

Run the backend in another terminal with the same activated environment:

```bash
cd backend
. .venv/bin/activate
uvicorn app.main:app --reload
```

Razorpay values must be test-mode credentials and are only needed for live integration actions. The synthetic batch uses a simulated client.

### Demo-only authentication

`POST /api/auth/login` is a **demo-only stub** that accepts any email and password. It is not an authentication boundary; production deployment must use Firebase Auth and merchant-level authorization.

### Useful endpoints

- `GET /health` — application and database liveness
- `POST /webhooks/razorpay` — signed webhook ingestion
- `GET /api/dashboard/summary` — aggregate recovery metrics
- `GET /api/dashboard/overview` — funnel, subscriptions, and exceptions
- `GET /api/recovery-metrics` — dashboard funnel and recovery totals
- `GET /api/subscriptions` — paginated subscription list
- `GET /api/exceptions` — unresolved cases with category and outcome filters
- `GET /api/exceptions/{outcome_id}/audit` — ordered audit trail for one case

### Run the synthetic batch

```bash
cd backend
python -m scripts.run_synthetic_batch --count 60 --seed 20260826
```

## Design Decisions

- Rules run before the LLM so clear Razorpay decline codes are classified deterministically; the LLM is reserved for ambiguous failures.
- The guardrail is a pure deterministic function that enforces cancellation freezes, retry limits, and cooldowns independently of model output.
- Webhook ingestion uses the Razorpay event ID as a Postgres unique key and `ON CONFLICT DO NOTHING`, so redeliveries create one payment event.
- Signature validation uses HMAC-SHA256 over the raw request body before JSON parsing, and rejects missing or invalid signatures.
- The executor commits the guardrail verdict and requested action audit before calling the injected Razorpay client.

## Testing

The backend tests cover health and dashboard endpoints, diagnosis rules and LLM fallback, policy decisions, guardrail limits and freezes, executor ordering with a simulated client, retry scheduling and concurrency, synthetic metrics, webhook signature validation, and webhook idempotency. The frontend build is checked separately in CI.

## What I'd Build Next

- Replace the demo auth stub with Firebase Auth and merchant-level access control.
- Replace the simulated batch client with recorded Razorpay test-mode fixtures.
- Add Cloud Run, Cloud SQL, and managed worker deployment configuration.
- Add richer filters and audit-log drill-down for exception handling.

## License

MIT
