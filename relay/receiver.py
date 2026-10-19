"""Demo receiver: validate signatures, fail twice, then acknowledge.

The in-memory deduplication is deliberately only a demo; a production consumer
must persist delivery IDs together with its side effects in one transaction.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import threading
import time

from relay.worker import signature

attempts = {}
accepted = set()
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        with lock:
            self.reply(200, {"accepted": len(accepted), "attempts": sum(attempts.values())})

    def do_POST(self):
        if self.path != "/hook":
            return self.reply(404, {})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            timestamp = int(self.headers.get("X-Relay-Timestamp", "0"))
        except ValueError:
            return self.reply(400, {})
        if not 0 < length <= 32768 or abs(time.time() - timestamp) > 300:
            return self.reply(400, {})
        body = self.rfile.read(length)
        expected = signature(os.environ["RELAY_SIGNING_SECRET"], timestamp, body)
        if not hmac.compare_digest(expected, self.headers.get("X-Relay-Signature", "")):
            return self.reply(401, {})
        ident = self.headers.get("X-Relay-Id", "")
        if len(ident) != 32:
            return self.reply(400, {})
        with lock:
            attempts[ident] = attempts.get(ident, 0) + 1
            if attempts[ident] <= 2:
                return self.reply(503, {"reason": "controlled demo failure"})
            duplicate = ident in accepted
            accepted.add(ident)
            self.reply(200, {"accepted": True, "duplicate": duplicate})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
