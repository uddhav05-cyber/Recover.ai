"""Entrypoint for the delayed-retry worker: ``python -m app.worker.run``.

Runs the Phase-4 poller against the app's Postgres, executing due scheduled
retries with ``SELECT ... FOR UPDATE SKIP LOCKED`` concurrency safety. Requires
Razorpay test-mode credentials in ``backend/.env`` (it reaches Razorpay to
re-check subscription status / send payment links).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from app.config import get_settings
from app.db.session import SessionLocal
from app.integrations.razorpay import RazorpayClient
from app.worker.retry_scheduler import poll_forever

logger = logging.getLogger("app.worker.run")


async def _main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    client = RazorpayClient.from_settings(settings)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # not supported on Windows
            loop.add_signal_handler(sig, stop.set)

    logger.info("retry worker starting; polling every 30s")
    try:
        await poll_forever(SessionLocal, client=client, interval=30.0, stop=stop)
    finally:
        await client.aclose()
        logger.info("retry worker stopped")


if __name__ == "__main__":
    asyncio.run(_main())
