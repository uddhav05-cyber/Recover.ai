# Recover.ai

RecoverAI detects failed Razorpay subscription payments, applies bounded recovery
actions, and records the full decision trail.

## Architecture

```mermaid
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

Every automated recovery action is persisted and audited before an external
payment call. The LLM can provide an ambiguous diagnosis, but it cannot bypass
the policy or guardrail.

## Local Setup

Requirements: Docker Desktop, Python 3.13, and Node.js 20+.

Start local Postgres from the repository root:

```powershell
docker compose up -d postgres
```

To run the complete containerized stack instead, use:

```powershell
docker compose up --build
```

The dashboard is then available at `http://localhost:8080`, the API at
`http://localhost:8000`, and migrations run automatically when the backend
starts.

Then configure the backend for the Compose database before running migrations:

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
# Set DATABASE_URL=postgresql+asyncpg://recoverai:recoverai@localhost:5432/recoverai
alembic upgrade head
uvicorn app.main:app --reload
```

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

The frontend proxies `/health` and `/api` to FastAPI on port 8000. Razorpay
credentials must be test-mode values and are only needed for live integration
actions; the synthetic batch uses a simulated client.

Useful endpoints:

- `GET /health` — application and database liveness
- `POST /webhooks/razorpay` — signed webhook ingestion
- `GET /api/dashboard/summary` — aggregate recovery metrics
- `GET /api/dashboard/overview` — funnel, subscriptions, and exceptions
- `POST /api/auth/login` — local Firebase Auth stub for the demo
- `GET /api/recovery-metrics` — dashboard funnel and recovery totals
- `GET /api/subscriptions` — paginated subscription list
- `GET /api/exceptions` — paginated unresolved recovery cases; supports
	`category` and `outcome` filters
- `GET /api/exceptions/{outcome_id}/audit` — ordered audit trail for one case

The frontend opens at the Vite URL shown by `npm run dev`. Use any email and
password for the local auth stub; production authentication is reserved for
Firebase Auth deployment.

## Phase 5: Synthetic Measurement

Run the deterministic demo batch from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_synthetic_batch --count 60 --seed 20260826
```

Baseline result from the 60-case batch:

- Cases processed: 60
- Recovered: 14 (23.3%)
- Amount recovered: Rs 10,886.00
- Still at risk: Rs 37,354.00
- Exceptions retained: 46

The batch uses a simulated Razorpay client. It exercises Diagnose -> Decide ->
Guardrail -> Act and the due-retry worker without making live payment calls.

After the batch completes, refresh the dashboard to inspect the generated
subscriptions, recovery funnel, recovered amounts, and exception list.

## Validation

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
cd ..\frontend
npm run build
```

## What I'd Build Next

- Replace the local auth stub with Firebase Auth and merchant-level access control.
- Replace the simulated batch client with recorded Razorpay test-mode fixtures.
- Add Cloud Run, Cloud SQL, and managed worker deployment configuration.
- Add richer filters and audit-log drill-down for exception handling.