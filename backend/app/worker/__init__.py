"""Background workers (Phase 4+).

* :mod:`app.worker.retry_scheduler` — the delayed-retry poller: it claims due
  ``scheduled`` retries with ``SELECT ... FOR UPDATE SKIP LOCKED`` (so a retry is
  never executed twice under concurrent workers) and runs each through the same
  Decide -> Guardrail -> Act ladder the executor uses.
* :mod:`app.worker.run` — the ``python -m app.worker.run`` entrypoint.
"""
