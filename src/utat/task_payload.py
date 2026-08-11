from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping


SCHEMA = "utat-task/v1"
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BRACE_PLACEHOLDER_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def load_payload_file(path: str | Path) -> Dict[str, Any]:
    p = Path(path).expanduser()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("payload 文件必须是 JSON 对象")
    return normalize_payload(data)


def normalize_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize the structured task contract while retaining legacy flat fields."""
    data = _strip_placeholders(dict(payload))
    issue = _mapping(data.get("issue"))
    task = _mapping(data.get("task"))
    source = _mapping(data.get("source"))
    test = _mapping(data.get("test"))
    build = _mapping(data.get("build"))
    callback = _mapping(data.get("callback"))

    def pick(*values: Any, default: Any = "") -> Any:
        for value in values:
            if value is not None and value != "":
                return value
        return default

    out = dict(data)
    out["schema"] = pick(data.get("schema"), SCHEMA, default=SCHEMA)
    out["workspace_id"] = pick(data.get("workspace_id"), issue.get("workspace_id"), task.get("workspace_id"), source.get("workspace_id"), callback.get("workspace_id"))
    out["issue_id"] = pick(data.get("issue_id"), issue.get("id"))
    out["root_issue_id"] = pick(data.get("root_issue_id"), issue.get("root_id"), issue.get("root_issue_id"))
    out["app_issue_id"] = pick(data.get("app_issue_id"), issue.get("app_id"), issue.get("app_issue_id"))
    out["task_type"] = str(pick(data.get("task_type"), task.get("type"), task.get("task_type"))).upper()
    out["app_name"] = pick(data.get("app_name"), task.get("app_name"), task.get("app"))
    out["node_id"] = pick(data.get("node_id"), data.get("target_node"), task.get("node_id"), task.get("target_node"), task.get("node"))
    out["repo"] = pick(
        data.get("repo"),
        data.get("repo_url"),
        source.get("repo"),
        source.get("url"),
        source.get("repo_url"),
        task.get("repo"),
        task.get("repo_url"),
    )
    out["branch"] = pick(data.get("branch"), source.get("branch"), task.get("branch"), default="master")
    out["commit"] = pick(data.get("commit"), source.get("commit"), task.get("commit"), default="")
    out["project_root"] = pick(data.get("project_root"), source.get("project_root"), task.get("project_root"), default="")
    out["execution_mode"] = pick(data.get("execution_mode"), data.get("validation_mode"), task.get("execution_mode"), task.get("validation_mode"), default="full")
    out["validation_mode"] = out["execution_mode"]
    out["no_code_update"] = _bool(pick(data.get("no_code_update"), source.get("no_code_update"), task.get("no_code_update"), default=False))

    scope = pick(data.get("test_scope"), test.get("scope"), default="")
    out["test_scope"] = scope
    out["suite"] = pick(data.get("suite"), test.get("suite"), default="")
    out["spec_ids"] = pick(data.get("spec_ids"), test.get("spec_ids"), default=[])
    out["at_path"] = pick(data.get("at_path"), test.get("at_path"), test.get("testdir"), default="")
    out["test_script"] = pick(data.get("test_script"), test.get("script"), test.get("test_script"), task.get("test_script"), default="")
    out["test_command"] = pick(data.get("test_command"), test.get("command"), task.get("test_command"), default="")
    out["at_command"] = pick(data.get("at_command"), test.get("at_command"), default="")
    out["app_command"] = pick(data.get("app_command"), task.get("app_command"), build.get("app_command"), test.get("app_command"), default="")

    # Keep build commands explicit. Different applications have different build systems.
    for key in ("dependency_command", "build_command", "package_command", "install_command"):
        out[key] = pick(data.get(key), build.get(key), default="")
    out["build_enabled"] = _bool(pick(data.get("build_enabled"), build.get("enabled"), default=True))

    environment = {}
    environment.update(_mapping(data.get("environment")))
    environment.update(_mapping(data.get("env")))
    out["environment"] = {str(k): str(v) for k, v in environment.items()}
    out["env"] = dict(out["environment"])
    out["callback"] = callback or _mapping(data.get("callback"))
    return out


def validate_payload(payload: Mapping[str, Any]) -> None:
    required = {
        "issue_id": "issue.id",
        "task_type": "task.type",
        "app_name": "task.app_name",
        "node_id": "task.node_id/target_node",
    }
    missing = [label for key, label in required.items() if not str(payload.get(key) or "").strip()]
    task_type = str(payload.get("task_type") or "").upper()
    if task_type not in {"AT", "UT"}:
        missing.append("task.type(AT|UT)")
    if missing:
        raise ValueError("任务 payload 缺少必填字段：" + ", ".join(missing))


def resolve_environment(payload: Mapping[str, Any]) -> Dict[str, str]:
    """Resolve ${NAME}; fail instead of passing unresolved placeholders to tests."""
    raw = _mapping(payload.get("environment")) or _mapping(payload.get("env"))
    resolved: Dict[str, str] = {}
    unresolved = []
    for key, value in raw.items():
        text = str(value)

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in __import__("os").environ:
                unresolved.append(name)
                return match.group(0)
            return __import__("os").environ[name]

        text = _PLACEHOLDER_RE.sub(replace, text)
        brace = _BRACE_PLACEHOLDER_RE.match(text)
        if brace:
            name = brace.group(1)
            if name in __import__("os").environ:
                text = __import__("os").environ[name]
            else:
                unresolved.append(name)
        if text.startswith("${") or brace:
            continue
        resolved[str(key)] = text
    if unresolved:
        names = ", ".join(sorted(set(unresolved)))
        raise ValueError(f"环境变量未解析，禁止启动测试：{names}")
    return resolved

_PLACEHOLDER_VALUES = {"可选", "默认", "无", "待填写", "UT脚本可选", "AT路径可选", "不需要", "无需", "none", "null", "N/A", "n/a"}


def _strip_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        return "" if value.strip() in _PLACEHOLDER_VALUES else value
    if isinstance(value, Mapping):
        return {k: _strip_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_placeholders(v) for v in value]
    return value


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
