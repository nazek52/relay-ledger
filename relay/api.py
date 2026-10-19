import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from starlette.types import ASGIApp

from relay.store import Conflict, Store


class BodyLimit:
    """Bound streamed requests, even when Content-Length is absent or false."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > 32768:
                return await JSONResponse({"detail": "Body exceeds 32 KiB"}, 413)(
                    scope, receive, send
                )
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, bounded, send)


class Envelope(BaseModel):
    event: str
    data: dict


def create_app(path=None, token=None):
    token = token or os.environ.get("RELAY_TOKEN", "")
    if len(token) < 16:
        raise RuntimeError("Set RELAY_TOKEN to a random value of at least 16 characters")
    store = Store(path or os.getenv("RELAY_DB", "relay.db"))
    app = FastAPI(title="Relay Ledger", version="1.0.0")
    app.add_middleware(BodyLimit)

    def authorize(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/health")
    def health():
        store.counts()
        return {"status": "ready"}

    def public(row):
        return {
            k: v
            for k, v in row.items()
            if k not in ("lease", "lease_until", "digest", "payload", "key")
        }

    @app.post("/deliveries", dependencies=[Depends(authorize)])
    def create(body: Envelope, idempotency_key: str = Header(min_length=1, max_length=128)):
        if not body.event.strip() or len(body.event) > 128:
            raise HTTPException(422, "Event name must contain 1–128 characters")
        try:
            row, created = store.enqueue(idempotency_key, body.model_dump())
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, "Payload must be finite JSON") from exc
        return JSONResponse(public(row), 202 if created else 200)

    @app.get("/deliveries", dependencies=[Depends(authorize)])
    def list_deliveries():
        return [public(row) for row in store.list()]

    @app.get("/deliveries/{ident}", dependencies=[Depends(authorize)])
    def delivery(ident: str):
        row = store.get(ident)
        if row is None:
            raise HTTPException(404, "Delivery not found")
        return {**public(row), "history": store.history(ident)}

    @app.get("/metrics", dependencies=[Depends(authorize)], response_class=PlainTextResponse)
    def metrics():
        counts = store.counts()
        return "# TYPE relay_deliveries gauge\n" + "".join(
            f'relay_deliveries{{state="{state}"}} {counts.get(state, 0)}\n'
            for state in ("pending", "running", "delivered", "dead")
        )

    return app
