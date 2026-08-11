from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .multica_client import MulticaClient


def _fmt(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value if value else default
    return str(value)


def ut_result_metric_lines(result: Dict[str, Any]) -> List[str]:
    m = result.get("metrics") or {}
    return [
        f"- metric_source: {_fmt(m.get('metric_source'))}",
        f"- total: {_fmt(m.get('total'), '0')}",
        f"- passed: {_fmt(m.get('passed'), '0')}",
        f"- failed: {_fmt(m.get('failed'), '0')}",
        f"- failed_cases: {_fmt(m.get('failed_cases'), '0')}",
        f"- crashed: {_fmt(m.get('crashed'), '0')}",
        f"- pass_rate: {_fmt(m.get('pass_rate'), '0.0')}",
        f"- case_metric_status: {_fmt(m.get('case_metric_status'))}",
        f"- line_coverage: {_fmt(m.get('line_coverage'))}",
        f"- function_coverage: {_fmt(m.get('function_coverage'))}",
    ]


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
        lines += ut_result_metric_lines(result)
    lines += ["", "附件：", *[f"- {Path(p).name}" for p in result.get("artifacts") or []]]
    return "\n".join(lines)


def upload_result(multica: MulticaClient, result: Dict[str, Any]) -> None:
    issue_id = result.get("issue_id")
    if not issue_id:
        return
    attachments = [p for p in result.get("artifacts") or [] if Path(p).exists()]
    multica.comment_add(issue_id, build_final_comment(result), attachments=attachments, temp_dir=Path(attachments[0]).parent if attachments else Path.cwd())
    # 执行失败不是流程阻塞：只要已经产生结果和日志，就将执行 issue 置为 done，
    # 失败原因保留在评论/附件/metadata 中，队长继续汇总和触发邮件。
    multica.issue_status(issue_id, "done")
