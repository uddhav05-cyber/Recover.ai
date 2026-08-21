# RecoverAI — Build Brief for Claude Code

Paste this whole file to Claude Code as your starting prompt (or save it as `CLAUDE.md` in your repo root so it has persistent context across the session).

---

## Project

**RecoverAI** — an autonomous agent that detects failed/at-risk Razorpay subscription payments, diagnoses the root cause, decides a bounded recovery action, executes it, and logs a full audit trail. Built for the Razorpay AI Buildathon, Track 03 (AI Revenue Recovery).

Judged on: public GitHub repo quality, a 5-min demo video, and architecture. No resume screen. Must show *measured* money recovered on a batch, with compliant escalation, stopping rules, and an audit trail — not just a demo toy.

## My existing stack (use this, don't introduce new tools)

- Backend: FastAPI (async), Python
- Agent orchestration: LangChain (or LangGraph if it fits the state machine better)
- LLM: Gemini 2.5 Flash via Vertex AI
- DB: Postgres (Cloud SQL, but use local Postgres via Docker for dev)
- Background jobs: Cloud Tasks or Celery+Redis — pick whichever is faster to set up locally
- Frontend: React + TypeScript + Tailwind
- Auth/hosting target: Firebase Auth + Cloud Run (not needed for local dev, just keep deploy-compatible)
- Payments: Razorpay Subscriptions API, Webhooks, Payment Links, test mode only

## Build order (please build in this sequence, confirming with me before moving to the next phase)

### Phase 1 — Foundation
1. Repo scaffold: FastAPI backend (`/backend`), React frontend (`/frontend`), Docker Compose for local Postgres
2. DB schema + migrations (Alembic) for: `merchants`, `subscriptions`, `payment_events`, `diagnoses`, `recovery_actions`, `recovery_outcomes`, `audit_log`
3. `.env.example` with placeholders for Razorpay test keys, Gemini/Vertex credentials, DB URL — never commit real secrets
4. Razorpay test-mode account wiring: create a subscription, verify I can trigger a `payment.failed` webhook manually

### Phase 2 — Webhook ingestion (get this rock solid before anything else)
1. FastAPI route that verifies Razorpay's HMAC webhook signature — reject anything that fails verification
2. The handler does nothing but validate + enqueue the raw payload — no synchronous processing in the webhook handler itself
3. Idempotency: dedupe on Razorpay's `event_id`, since Razorpay retries webhook delivery on non-2xx responses. Add a unique constraint in the DB and short-circuit duplicates.
4. Write a test that fires the same webhook payload twice and confirms only one `payment_events` row is created

### Phase 3 — Diagnose → Decide → Act agent loop
1. **Diagnose**: classify the failure into a small fixed set of root-cause categories (insufficient funds, expired/invalid card, bank server error, mandate revoked, other) using the Razorpay decline code + payment history. Use Gemini only for ambiguous cases — a rules table should handle the clear-cut ones.
2. **Decide**: a policy layer that maps diagnosis → intervention (retry with a specific delay, send a payment link via Smart Collect, escalate to manual review, mark dead). This should be inspectable/testable as its own function, not buried in a prompt.
3. **Guardrail engine**: a deterministic module (not LLM-driven) enforcing: max retry attempts, cooldown windows, no action after explicit mandate cancellation, every action logged to `audit_log` before execution. Write unit tests for this module specifically — it's the centerpiece of the submission.
4. **Act**: execute the chosen action against Razorpay's test-mode API, log the response
5. Log every LLM diagnosis reasoning string to the DB, even though the guardrail — not the LLM — has final say on execution

### Phase 4 — Scheduler for delayed retries
1. Background worker that polls `recovery_actions` for `scheduled_at <= now()` and executes them
2. Confirm a scheduled retry doesn't fire twice under concurrent workers (use a `SELECT ... FOR UPDATE SKIP LOCKED` or equivalent)

### Phase 5 — Synthetic data + measurement
1. A generator script producing ~50-100 realistic failed-payment events across the failure categories (don't hand-pick easy cases)
2. Run the full pipeline against this batch, output: recovery %, ₹ recovered, ₹ still at risk, and an honest exception list of unresolved cases

### Phase 6 — Dashboard
1. React frontend: list of subscriptions with status, a recovery funnel view (failed → retried → recovered/dead), an exceptions table
2. Simple polling or Firestore listener for live updates — doesn't need to be fancy, needs to be legible in a demo video

### Phase 7 — Polish for submission
1. README with architecture diagram, setup instructions, metrics section, "what I'd build next"
2. Clean commit history (conventional commits), at least one PR-merged branch
3. Remove dead code, TODOs, commented-out blocks

## Constraints / things to enforce throughout

- Every money-moving action must be explainable, bounded, and gated — never let the LLM directly trigger a Razorpay API call without passing through the guardrail
- Test-mode Razorpay only
- Idempotency on webhooks is non-negotiable, build and test it before moving to Phase 3
- Keep the diagnosis category set small (4-5 categories) — don't over-engineer classification at the expense of finishing the loop

## What to ask me before proceeding

- Confirm local Postgres/Docker setup works before writing schema
- Show me the webhook idempotency test passing before starting Phase 3
- Show me the guardrail unit tests before wiring in the Act step
- Check in before starting the frontend — I may want to adjust dashboard scope depending on remaining time before Sept 5
