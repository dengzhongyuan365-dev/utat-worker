from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class AppSpec:
    app_name: str
    repo: str = ""
    branch: str = "master"
    validation_mode: str = "full"
    test_scope: str = "全部 suite"
    route: str = ""
    run_types: List[str] | None = None


def normalize_run_types(value: str | List[str] | None) -> List[str]:
    """Normalize user-facing AT/UT execution type declarations."""
    if value is None:
        return []
    if isinstance(value, list):
        tokens = value
    else:
        text = str(value).upper()
        text = text.replace("ＡＴ", "AT").replace("ＵＴ", "UT")
        tokens = re.split(r"[^A-Z]+", text)
    out: List[str] = []
    for token in tokens:
        t = str(token).upper().strip()
        if t in {"AT", "UT"} and t not in out:
            out.append(t)
    return out


def infer_run_types_from_title(title: str) -> List[str]:
    t = (title or "").upper().replace("ＡＴ", "AT").replace("ＵＴ", "UT")
    if re.search(r"(^|[^A-Z0-9])AT\s*[-_/+、和与&]*\s*UT\s*[-_]", t) or t.startswith("AT-UT-"):
        return ["AT", "UT"]
    if re.match(r"^AT-", t) and not re.match(r"^AT-UT-", t):
        return ["AT"]
    if re.match(r"^UT-", t):
        return ["UT"]
    return ["AT", "UT"]


def infer_run_types_from_text(text: str) -> List[str]:
    """Return explicit content-declared run types, or [] when not declared."""
    if not text:
        return []
    raw = text.strip()
    normalized = raw.upper().replace("ＡＴ", "AT").replace("ＵＴ", "UT")
    # Explicit key-value declarations have the highest priority.
    patterns = [
        r"(?:执行类型|测试类型|运行类型|任务类型|RUN_TYPES?|TEST_TYPES?)\s*[:：=]\s*([^\n\r]+)",
        r"(?:只跑|仅跑|只执行|仅执行)\s*(AT|UT)",
    ]
    for pat in patterns:
        m = re.search(pat, normalized, re.I)
        if m:
            types = normalize_run_types(m.group(1))
            if types:
                return types
    # Natural language declarations.
    compact = re.sub(r"\s+", "", normalized)
    both_patterns = [
        r"AT[、,，/+和与&]*UT(都|全部)?(跑|执行|测试)",
        r"(跑|执行|测试)(AT[、,，/+和与&]*UT)",
        r"AT-UT",
        r"AT/UT",
    ]
    if any(re.search(p, compact, re.I) for p in both_patterns):
        return ["AT", "UT"]
    if re.search(r"(只跑|仅跑|只执行|仅执行)AT", compact, re.I):
        return ["AT"]
    if re.search(r"(只跑|仅跑|只执行|仅执行)UT", compact, re.I):
        return ["UT"]
    return []


def resolve_run_types(title: str, desc: str = "") -> List[str]:
    explicit = infer_run_types_from_text(desc)
    return explicit or infer_run_types_from_title(title)


def flatten_children(children: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for x in children.get("unstaged") or []:
        out.append(x)
    for st in children.get("stages") or []:
        for x in st.get("issues") or []:
            out.append(x)
    return out


def parse_root_description(desc: str) -> List[AppSpec]:
    """Parse loose Chinese issue description. Supports bullet lines and key-value blocks."""
    specs: List[AppSpec] = []
    current: Dict[str, str] = {}
    table_header: List[str] | None = None

    def clean_cell(v: str) -> str:
        v = v.strip()
        v = re.sub(r"^`|`$", "", v)
        # markdown link: [text](url)
        m = re.search(r"\((https?://[^)]+)\)", v)
        if m:
            return m.group(1)
        return v

    def extract_url(v: str) -> str:
        m = re.search(r"https?://[^\s)]+", v)
        return m.group(0) if m else ""

    def parse_table_row(cells: List[str]) -> None:
        nonlocal table_header
        if not cells or any("---" in c for c in cells):
            return
        header_like = (
            len(cells) >= 5
            and ("序号" in cells[0] or "项目/仓库" in cells[1] or "repo_id" in cells[1].lower())
        )
        if header_like:
            table_header = cells
            return
        if not table_header:
            return
        # 真实表格固定列：序号 / repo_id / 平台 / 仓库地址 / 分支 / 状态
        if len(cells) < 6:
            return
        app = clean_cell(cells[1])
        status = clean_cell(cells[5]).lower()
        if status != "done":
            return
        repo = extract_url(cells[3]) or clean_cell(cells[3])
        branch = clean_cell(cells[4]) or "master"
        if not app or app == "repo_id":
            return
        specs.append(AppSpec(app_name=app, repo=repo, branch=branch))

    def flush():
        nonlocal current
        app = current.get("app") or current.get("应用") or current.get("应用名称")
        if app:
            specs.append(AppSpec(
                app_name=app.strip(),
                repo=(current.get("repo") or current.get("仓库") or current.get("REPO") or "").strip(),
                branch=(current.get("branch") or current.get("分支") or current.get("BRANCH") or "master").strip(),
                validation_mode=(current.get("mode") or current.get("验证模式") or current.get("VALIDATION_MODE") or "full").strip(),
                test_scope=(current.get("scope") or current.get("测试范围") or current.get("TEST_SCOPE") or "全部 suite").strip(),
                route=(current.get("node") or current.get("节点") or current.get("route") or "").strip(),
            ))
        current = {}

    for raw in desc.splitlines():
        line = raw.strip().strip("-•* ")
        if not line:
            flush()
            continue
        # markdown table row
        if line.startswith("|") and line.endswith("|"):
            cells = [clean_cell(c) for c in line.strip("|").split("|")]
            parse_table_row(cells)
            current = {}
            continue
        # compact line: app / repo / branch / mode / node
        if "/" in line and ("github.com" in line or "gerrit" in line):
            parts = [p.strip() for p in line.split("/")]
            # do not split URL reliably; fallback regex
        m = re.search(r"应用[:：]\s*([^\s,，]+)", line, re.I)
        if m:
            if current.get("app"):
                flush()
            current["app"] = m.group(1)
        for key in ["REPO", "repo", "仓库", "BRANCH", "branch", "分支", "VALIDATION_MODE", "验证模式", "TEST_SCOPE", "测试范围", "节点", "node"]:
            mm = re.search(rf"{re.escape(key)}[:：]\s*([^\s,，]+)", line, re.I)
            if mm:
                current[key] = mm.group(1)
    flush()
    # dedupe preserving order
    seen = set(); out=[]
    for s in specs:
        if s.app_name not in seen:
            seen.add(s.app_name); out.append(s)
    return out


def child_by_title(children: List[Dict[str, Any]], title: str) -> Optional[Dict[str, Any]]:
    for c in children:
        if c.get("title") == title:
            return c
    return None


def find_exec_children(app_children: List[Dict[str, Any]], timestamp: str, app: str) -> Dict[str, Optional[Dict[str, Any]]]:
    return {
        "AT": child_by_title(app_children, f"AT-{timestamp}-{app}"),
        "UT": child_by_title(app_children, f"UT-{timestamp}-{app}"),
    }


def extract_timestamp_from_root_title(title: str) -> str:
    m = re.search(r"(?:AT-UT|AT|UT)-(\d{12})", title or "", re.I)
    return m.group(1) if m else "000000000000"
