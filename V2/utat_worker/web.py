from __future__ import annotations

import html
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from .config import WorkerConfig
from .db import ACTIVE_STATES, QueueDB


def _fmt_ts(v: Any) -> str:
    try:
        if not v:
            return "-"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(v)))
    except Exception:
        return "-"


def _issue_link(issue_id: str) -> str:
    if not issue_id:
        return "-"
    url = f"https://agent-dev.uniontech.com/v25/issues/{html.escape(issue_id)}"
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{html.escape(issue_id[:8])}</a>'


def _task_row(t: Dict[str, Any]) -> str:
    state = html.escape(str(t.get("state") or ""))
    cls = "active" if state in ACTIVE_STATES else "done" if state == "completed" else "bad" if state in {"callback_failed", "deleted", "orphan"} else ""
    progress = int(t.get("progress") or 0)
    msg = html.escape(str(t.get("message") or t.get("error") or ""))
    return f"""
<tr class="{cls}">
  <td>{html.escape(str(t.get('node_id') or ''))}</td>
  <td>{html.escape(str(t.get('task_type') or ''))}</td>
  <td>{html.escape(str(t.get('app_name') or ''))}</td>
  <td>{_issue_link(str(t.get('issue_id') or ''))}</td>
  <td>{html.escape(str(t.get('attempt') or ''))}</td>
  <td><span class="pill {cls}">{state}</span></td>
  <td>{html.escape(str(t.get('current_step') or '-'))}</td>
  <td><div class="bar"><span style="width:{progress}%"></span></div><b>{progress}%</b></td>
  <td class="msg">{msg}</td>
  <td>{_fmt_ts(t.get('created_at'))}</td>
  <td>{_fmt_ts(t.get('updated_at'))}</td>
  <td>{html.escape(str(t.get('archive_path') or '-'))}</td>
</tr>"""


def render_page(status: Dict[str, Any]) -> str:
    tasks: List[Dict[str, Any]] = status.get("tasks") or []
    active = [t for t in tasks if t.get("state") in ACTIVE_STATES]
    running = [t for t in active if t.get("state") == "running"]
    rows = "\n".join(_task_row(t) for t in tasks) or '<tr><td colspan="12">暂无任务</td></tr>'
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>AT/UT Worker 进度</title>
<style>
body {{ margin:0; padding:22px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif; background:#f5f7fb; color:#111827; }}
.header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:18px; margin-bottom:16px; }}
h1 {{ margin:0 0 8px; font-size:22px; }}
.meta {{ color:#64748b; font-size:13px; line-height:1.6; }}
.cards {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:16px 0; }}
.card {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:14px; box-shadow:0 2px 8px rgba(15,23,42,.04); }}
.label {{ color:#64748b; font-size:12px; }}
.value {{ font-size:22px; font-weight:700; margin-top:6px; }}
table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid #e5e7eb; border-radius:10px; overflow:hidden; }}
th,td {{ border:1px solid #e5e7eb; padding:8px 9px; font-size:12px; vertical-align:middle; }}
th {{ background:#2563eb; color:white; white-space:nowrap; }}
tr.active td {{ background:#fff7ed; }}
tr.done td {{ background:#f0fdf4; }}
tr.bad td {{ background:#fef2f2; }}
.pill {{ display:inline-block; padding:3px 8px; border-radius:999px; background:#e5e7eb; font-weight:700; }}
.pill.active {{ background:#fed7aa; color:#c2410c; }}
.pill.done {{ background:#bbf7d0; color:#15803d; }}
.pill.bad {{ background:#fecaca; color:#b91c1c; }}
.bar {{ width:120px; height:8px; background:#e5e7eb; border-radius:999px; display:inline-block; vertical-align:middle; overflow:hidden; margin-right:6px; }}
.bar span {{ display:block; height:100%; background:#2563eb; }}
.msg {{ max-width:300px; word-break:break-all; }}
a {{ color:#2563eb; text-decoration:none; font-weight:600; }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>AT/UT Worker 进度</h1>
    <div class="meta">自动刷新：5 秒　当前时间：{now}<br>DB：{html.escape(str(status.get('db') or ''))}</div>
  </div>
  <div class="meta">接口：<a href="/api/status">/api/status</a></div>
</div>
<div class="cards">
  <div class="card"><div class="label">节点</div><div class="value">{html.escape(str(status.get('node_id') or ''))}</div></div>
  <div class="card"><div class="label">Active</div><div class="value">{len(active)}</div></div>
  <div class="card"><div class="label">Running</div><div class="value">{len(running)}</div></div>
  <div class="card"><div class="label">Total</div><div class="value">{len(tasks)}</div></div>
</div>
<table>
<thead><tr><th>节点</th><th>类型</th><th>应用</th><th>Issue</th><th>Attempt</th><th>状态</th><th>当前步骤</th><th>进度</th><th>说明</th><th>创建</th><th>更新</th><th>归档</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body>
</html>"""


class StatusServer:
    def __init__(self, cfg: WorkerConfig, db: QueueDB):
        self.cfg = cfg
        self.db = db

    def status(self, issue_id: str = "") -> Dict[str, Any]:
        rows = self.db.list_tasks(issue_id=issue_id, limit=500)
        active = [r for r in rows if r.get("state") in ACTIVE_STATES]
        return {"ok": True, "db": str(self.cfg.db_path), "node_id": self.cfg.node_id, "active_count": len(active), "tasks": rows}

    def handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def _send(self, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                issue_id = (qs.get("issue_id") or [""])[0]
                data = outer.status(issue_id=issue_id)
                if parsed.path == "/api/status":
                    self._send(json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8"), "application/json; charset=utf-8")
                    return
                if parsed.path in {"/", "/index.html"}:
                    self._send(render_page(data).encode("utf-8"))
                    return
                self.send_response(404)
                self.end_headers()

        return Handler

    def serve(self, host: str, port: int) -> None:
        httpd = ThreadingHTTPServer((host, port), self.handler())
        print(f"AT/UT Worker status page: http://{host}:{port}")
        httpd.serve_forever()
