from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, Optional


class APIClient:
    def __init__(self, base_url: str, token_env: str = "UTAT_SERVER_TOKEN"):
        self.base_url = base_url.rstrip("/")
        self.token = os.environ.get(token_env, "")

    def post(self, path: str, data: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=raw, method="POST", headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get(self, path: str, timeout: int = 30) -> Dict[str, Any]:
        req = urllib.request.Request(self.base_url + path, method="GET")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
