# Decisions and delivery guarantees

## 1. SQLite for a bounded single-host queue
WAL permits concurrent readers while short BEGIN IMMEDIATE transactions serialize writers. Enqueue and claim decisions are made inside the write transaction. No HTTP request runs while holding a database transaction. This intentionally trades horizontal write scaling for inspectable local behavior. Multi-host storage would require a different queue/locking implementation.

## 2. Leases and fencing
Each claim increments the attempt counter and records a unique lease token. Another worker can reclaim an expired lease. Completion updates only a running row with the same lease token; a stale worker cannot overwrite a newer claim. Timeout is five seconds and lease duration is thirty seconds in the provided worker. A stalled process can still perform duplicate external side effects after lease expiry; receiver idempotency is required.

## 3. At-least-once semantics
The enqueue key is global within this single-tenant instance. Identical canonical JSON returns the original ID; different content returns a conflict. Successful receiver response and local success recording are not a distributed transaction. Crash between them causes a retry. Do not describe this as exactly-once delivery.

## 4. Explicit retry classification
Network failures, 408, 429, and 5xx retry; other statuses, including redirects, are terminal. Backoff is min(300, 2^attempt) plus uniform [0,1) seconds. Five claims exhaust the budget, even if all workers crashed. Dead letters remain queryable and are not automatically replayed. Retry-After support and operator replay tooling are future work.

## 5. Trust boundaries
The producer supplies event content but cannot select a target URL. The target is trusted operator configuration. TLS and network egress controls are still needed outside this local lab. The HMAC input is `unix_timestamp + '.' + exact_body_bytes`. Receivers should verify signature, timestamp freshness, and a durable delivery-ID record. The demo receiver stores its deduplication set in memory and is not production infrastructure.

[SQLite transaction behavior](https://www.sqlite.org/lang_transaction.html)
