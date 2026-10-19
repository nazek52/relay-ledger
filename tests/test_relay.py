from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import httpx
import pytest
from fastapi.testclient import TestClient

from relay.api import create_app
from relay.store import Conflict, Store
from relay.worker import deliver_one, signature


@pytest.fixture
def store(tmp_path):
    now = [1000.0]
    result = Store(tmp_path / "relay.db", clock=lambda: now[0])
    result.now = now
    return result


def test_idempotency_and_conflict(store):
    first, created = store.enqueue("same", {"b": 2, "a": 1})
    duplicate, repeated = store.enqueue("same", {"a": 1, "b": 2})
    assert created and not repeated and first["id"] == duplicate["id"]
    with pytest.raises(Conflict):
        store.enqueue("same", {"a": 2})


def test_concurrent_enqueue_and_claim(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: store.enqueue("shared", {"x": 1}), range(16)))
        jobs = list(pool.map(lambda _: store.claim(), range(16)))
    assert sum(created for _, created in rows) == 1
    assert sum(job is not None for job in jobs) == 1


def test_expired_worker_cannot_overwrite_new_worker(store):
    row, _ = store.enqueue("x", {})
    stale = store.claim()
    store.now[0] += 31
    current = store.claim()
    assert not store.finish(stale, 200)
    assert store.finish(current, 200)
    assert store.get(row["id"])["state"] == "delivered"
    assert store.history(row["id"])[0]["error"] == "lease expired"


def test_repeated_crashes_reach_dead_letter(store):
    row, _ = store.enqueue("x", {})
    for _ in range(5):
        assert store.claim()
        store.now[0] += 31
    assert store.claim() is None
    assert store.get(row["id"])["state"] == "dead"


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, "delivered"),
        (204, "delivered"),
        (301, "dead"),
        (400, "dead"),
        (408, "pending"),
        (429, "pending"),
        (503, "pending"),
        (None, "pending"),
    ],
)
def test_status_policy(store, status, expected):
    row, _ = store.enqueue("x", {})
    assert store.finish(store.claim(), status)
    assert store.get(row["id"])["state"] == expected
    assert store.claim() is None


def test_retry_budget_and_backoff(store):
    row, _ = store.enqueue("x", {})
    for attempt in range(1, 6):
        job = store.claim()
        assert job["attempts"] == attempt
        store.finish(job, 503)
        assert store.claim() is None
        store.now[0] += 2**attempt
    assert store.get(row["id"])["state"] == "dead"


def test_worker_signed_wire_payload(store):
    row, _ = store.enqueue("signed", {"event": "incident.opened", "data": {"id": 1}})

    def receiver(request):
        expected = hmac.new(
            b"secret",
            request.headers["X-Relay-Timestamp"].encode() + b"." + request.content,
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(expected, request.headers["X-Relay-Signature"])
        assert request.headers["X-Relay-Id"] == row["id"]
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(receiver)) as client:
        assert deliver_one(store, "https://receiver.test/hook", "secret", client)
    assert store.get(row["id"])["state"] == "delivered"


def test_network_failure_persists_retry(store):
    row, _ = store.enqueue("x", {})

    def timeout(request):
        raise httpx.ConnectTimeout("sensitive URL")

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        deliver_one(store, "https://receiver.test", "secret", client)
    assert store.get(row["id"])["last_error"] == "ConnectTimeout"


def test_api_auth_conflict_size_and_history(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db", "test-token-at-least-16"))
    assert client.get("/deliveries").status_code == 401
    headers = {"Authorization": "Bearer test-token-at-least-16", "Idempotency-Key": "test"}
    body = {"event": "incident.opened", "data": {}}
    first = client.post("/deliveries", json=body, headers=headers)
    assert first.status_code == 202
    assert client.post("/deliveries", json=body, headers=headers).status_code == 200
    assert (
        client.post("/deliveries", json={**body, "data": {"x": 1}}, headers=headers).status_code
        == 409
    )
    assert client.post("/deliveries", content=b"x" * 32769, headers=headers).status_code == 413
    detail = client.get("/deliveries/" + first.json()["id"], headers=headers).json()
    assert detail["history"] == [] and "payload" not in detail
    assert client.get("/health").status_code == 200


def test_persists_after_reopen(tmp_path):
    path = tmp_path / "persistent.db"
    first, _ = Store(path).enqueue("restart", {})
    assert Store(path).get(first["id"])["state"] == "pending"
