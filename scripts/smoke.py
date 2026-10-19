"""Verify the real API -> durable queue -> worker -> signed receiver flow."""

import json
from pathlib import Path
import time
import urllib.request
import uuid

config = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines() if "=" in line)
headers = {
    "Authorization": "Bearer " + config["RELAY_TOKEN"],
    "Content-Type": "application/json",
    "Idempotency-Key": uuid.uuid4().hex,
}
body = json.dumps({"event": "demo.incident.opened", "data": {"synthetic": True}}).encode()


def request(path, data=None):
    req = urllib.request.Request("http://127.0.0.1:8011" + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.load(response)


status, row = request("/deliveries", body)
assert status == 202
status, duplicate = request("/deliveries", body)
assert status == 200 and duplicate["id"] == row["id"]
deadline = time.monotonic() + 40
while time.monotonic() < deadline:
    _, result = request("/deliveries/" + row["id"])
    if result["state"] == "delivered":
        assert [attempt["status"] for attempt in result["history"]] == [503, 503, 200]
        print(
            json.dumps(
                {
                    "verified": True,
                    "attempt_statuses": [503, 503, 200],
                    "idempotency": "same delivery ID",
                }
            )
        )
        break
    time.sleep(0.5)
else:
    raise SystemExit("Delivery did not complete within 40 seconds")
