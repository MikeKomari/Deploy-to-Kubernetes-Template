#!/usr/bin/env python3
"""Simple dependency-free backend for testing k8s deployments."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT") or os.environ.get("APP_PORT") or "3000")
NAME = os.environ.get("APP_NAME") or "simple-backend"
ENV = os.environ.get("ENVIRONMENT") or "dev"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        elif self.path == "/":
            self._respond(200, {
                "message": f"Hello from {NAME}!",
                "environment": ENV,
                "port": PORT,
            })
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"{NAME} listening on :{PORT} (env={ENV})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
