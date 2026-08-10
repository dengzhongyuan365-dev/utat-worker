from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import urlparse

from .queue_db import QueueDB


class APIServer:
    def __init__(self, db: QueueDB, host: str, port: int, token_env: str = "UTAT_SERVER_TOKEN"):
        self.db = db
        self.host = host
        self.port = int(port)
        self.token = os.environ.get(token_env, "")

    def serve_forever(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "utat-api/0.1"

            def _auth(self) -> bool:
                if not outer.token:
                    return True
                return self.headers.get("Authorization") == f"Bearer {outer.token}"

            def _read_json(self) -> Dict[str, Any]:
                n = int(self.headers.get("Content-Length") or "0")
                if n <= 0:
                    return {}
                return json.loads(self.rfile.read(n).decode("utf-8"))

            def _send(self, code: int, data: Any) -> None:
                raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if not self._auth():
                    return self._send(401, {"error": "unauthorized"})
                path = urlparse(self.path).path
                if path == "/healthz":
                    return self._send(200, {"ok": True})
                if path == "/api/v1/tasks":
                    return self._send(200, {"tasks": outer.db.list_exec()})
                return self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._auth():
                    return self._send(401, {"error": "unauthorized"})
                path = urlparse(self.path).path
                try:
                    body = self._read_json()
                    if path == "/api/v1/nodes/heartbeat":
                        outer.db.heartbeat_node(body["node_id"], hostname=body.get("hostname", ""), capabilities=body.get("capabilities") or {}, max_parallel=int(body.get("max_parallel", 1)))
                        return self._send(200, {"ok": True})
                    if path == "/api/v1/tasks/claim":
                        task = outer.db.claim_task(body["node_id"], body.get("capabilities") or {})
                        return self._send(200, {"task": task})
                    if path.startswith("/api/v1/tasks/") and path.endswith("/heartbeat"):
                        task_id = path.split("/")[4]
                        outer.db.update_task(task_id, phase=body.get("phase", "running"), pid=body.get("pid"), log_path=body.get("log_path", ""), status=body.get("status", "running"))
                        return self._send(200, {"ok": True})
                    if path.startswith("/api/v1/tasks/") and path.endswith("/complete"):
                        task_id = path.split("/")[4]
                        outer.db.complete_task(task_id, body.get("status", "failed"), body.get("result") or {})
                        return self._send(200, {"ok": True})
                    return self._send(404, {"error": "not found"})
                except Exception as e:
                    return self._send(500, {"error": str(e)})

            def log_message(self, fmt: str, *args: Any) -> None:
                print(f"[api] {self.address_string()} {fmt % args}")

        httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        print(f"utat api listening on http://{self.host}:{self.port}")
        httpd.serve_forever()
