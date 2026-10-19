# Verification record

Executed 2026-10-19 on Windows, Python 3.13.5 and Docker Desktop Linux containers.

- `python -m pytest -q`: **17 passed**. Covers concurrent idempotency/claim, stale-worker fencing, crash budget, retry classification/backoff, signatures, network failures, API authentication/body limits, and persistence after reopening.
- `docker compose up -d --build --wait`: completed with non-root processes and persistent SQLite storage.
- `python scripts/smoke.py`: real API → DB → worker → HMAC receiver flow passed; attempts were **503, 503, 200**. Duplicate enqueue returned the same delivery ID.
- GitHub workflow repeats unit tests and the Compose smoke scenario on Linux; inspect its current result in Actions rather than treating this local record as a cloud-run claim.

Not verified: production throughput, multi-host operation, TLS ingress, real external receivers, backup/restore, or long-running load. The Starlette test client emits an upstream deprecation notice for httpx; tests pass with the pinned versions.
