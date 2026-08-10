from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .multica_client import MulticaClient


def build_final_comment(result: Dict[str, Any]) -> str:
    typ = result.get("task_type")
    marker = "UT_FINAL_RESULT" if typ == "UT" else "AT_FINAL_RESULT"
    m = result.get("metrics") or {}
    lines = [
        f"[{marker}]",
        f"应用：{result.get('app','')}",
        f"状态：{result.get('status','')}",
        f"原因：{result.get('reason','')}",
        f"退出码：{result.get('exit_code')}",
        f"执行 issue：https://agent-dev.uniontech.com/v25/issues/{result.get('issue_id')}",
    ]
    if typ == "UT":
        lines += [
            f"失败数：{m.get('failed', '-')}",
            f"异常套件数：{m.get('crashed', '-')}",
            f"总套件数：{m.get('total_suites', '-')}",
            f"行覆盖率：{m.get('line_coverage', '-')}",
            f"函数覆盖率：{m.get('function_coverage', '-')}",
        ]
    lines += ["", "附件：", *[f"- {Path(p).name}" for p in result.get("artifacts") or []]]
    return "\n".join(lines)


def upload_result(multica: MulticaClient, result: Dict[str, Any]) -> None:
    issue_id = result.get("issue_id")
    if not issue_id:
        return
    attachments = [p for p in result.get("artifacts") or [] if Path(p).exists()]
    multica.comment_add(issue_id, build_final_comment(result), attachments=attachments)
    multica.issue_status(issue_id, "done" if result.get("status") == "done" else "blocked")
