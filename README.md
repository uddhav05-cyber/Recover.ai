# RecoverAI

AI-assisted recovery agent for failed Razorpay subscription payments.
**Diagnose (LLM) → Decide (policy) → Guardrail (deterministic gate) → Act.**
Every automated action is persisted and audited *before* an external payment call — the LLM can inform a diagnosis, but it can never bypass the policy or the guardrail.

<!-- [ ] Optional: add a static screenshot of the dashboard here once you have one.
     ![dashboard](docs/dashboard.png)
     Not required to publish — the written example below carries the pitch on its own. -->

## The problem

Failed subscription payments are usually handled with a blind, fixed retry schedule (day 1, day 3, day 7) regardless of *why* the payment failed. A card declined for "insufficient funds" behaves nothing like one declined for an unclear issuer-side reason — but naive retry treats them identically, wasting attempts on cases that were never recoverable and silently losing revenue on ones that were.

## Why Diagnose and Decide are separate steps

RecoverAI's Diagnose step uses an LLM to classify ambiguous decline reasons — raw gateway responses are often generic strings with no structured recoverability signal — into **recoverable now**, **recoverable later**, or **dead**.

The LLM's diagnosis is advisory only. The Decide step applies an explicit, human-authored policy to that diagnosis, and the Guardrail step is a separate, deterministic gate that checks confidence and policy compliance before *anything* is allowed to reach the Act step. If the LLM is uncertain, or its suggested action falls outside policy, the case is routed to a manual exception queue instead of acted on automatically. This is the part of the system that matters most: it's what stops a bad LLM call from ever triggering a real payment action.

**Example case** 
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

## Results (synthetic batch, seed 20260826, n=60)

| | RecoverAI | Naive fixed-schedule retry |
|---|---|---|
| Cases processed | 60 | 60 |
| Recovered | 14 (23.3%) | `[ ]` — run the same seed through a fixed-schedule retry with no diagnosis step and log this |
| Amount recovered | Rs 10,886.00 | `[ ]` |
| Still at risk | Rs 37,354.00 | `[ ]` |
| Exceptions correctly triaged for manual review | 46 | 0 — naive retry has no triage step |

The batch uses a simulated Razorpay client and exercises Diagnose → Decide → Guardrail → Act plus the due-retry worker without making live payment calls.

*If you don't fill in the baseline column, drop the second column and state the RecoverAI numbers on their own — but know that an unanchored percentage reads as unremarkable even when the underlying result is good. The comparison is 20 minutes of scripting against your existing synthetic client and is the highest-leverage addition to this file.*

## Local Setup

Requirements: Python 3.13 and Node.js 20+. Docker Desktop is optional.

### Windows Without Docker

The development environment can use the configured hosted Postgres database, so Docker is not needed for a local dashboard demo. From the repository root, run the backend setup once:

```
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
cd ..
.\scripts\start-local.ps1
```

Open `http://localhost:8080`. The launcher starts FastAPI on port 8000 and Vite on port 8080 in separate processes.

### With Docker

Start local Postgres from the repository root:

```
docker compose up -d postgres
```

To run the complete containerized stack instead, use:

```
docker compose up --build
```

The dashboard is then available at `http://localhost:8080`, the API at `http://localhost:8000`, and migrations run automatically when the backend starts.

Then configure the backend for the Compose database before running migrations:

```
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

```
cd frontend
npm install
npm run dev
```

The frontend proxies `/health` and `/api` to FastAPI on port 8000. Razorpay credentials must be test-mode values and are only needed for live integration actions; the synthetic batch uses a simulated client.

**Useful endpoints:**

- `GET /health` — application and database liveness
- `POST /webhooks/razorpay` — signed webhook ingestion
- `GET /api/dashboard/summary` — aggregate recovery metrics
- `GET /api/dashboard/overview` — funnel, subscriptions, and exceptions
- `POST /api/auth/login` — local Firebase Auth stub for the demo
- `GET /api/recovery-metrics` — dashboard funnel and recovery totals
- `GET /api/subscriptions` — paginated subscription list
- `GET /api/exceptions` — paginated unresolved recovery cases; supports `category` and `outcome` filters
- `GET /api/exceptions/{outcome_id}/audit` — ordered audit trail for one case

The frontend opens at the Vite URL shown by `npm run dev`. Use any email and password for the local auth stub; production authentication is reserved for Firebase Auth deployment.

### Running the synthetic demo batch

```
cd backend
.\.venv\Scripts\python.exe -m scripts.run_synthetic_batch --count 60 --seed 20260826
```

After the batch completes, refresh the dashboard to inspect the generated subscriptions, recovery funnel, recovered amounts, and exception list.

## Validation

```
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

## License

MIT
