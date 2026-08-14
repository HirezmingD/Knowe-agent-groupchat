"""v1.0.34 省 token 机制 — 工具结果流类型化压缩（纯函数，无网络无存储）。

入口 compress() 面向三处工具结果接入点（agent_loop / runtime / deepseek）：

  compress(content) -> (out, meta)
    meta = {method, before_chars, after_chars}
    method: "compact_json" | "fold_log" | "passthrough"

工程纪律（与架构设计 2.1 一致）：
1. 确定性/幂等：同输入必同输出；compress(compress(x)) == compress(x)
2. fail-closed：任何解析失败 -> 透传原文，method="passthrough"
3. 诚实零：压缩产物不比原文短 -> 透传原文，method="passthrough"
4. 阈值全部走 config.py（KNOWE_TOOL_COMPRESS_*），禁止散落写死

设计取舍（和洲拍板）：只折叠"连续字节级完全相同的行"，不做重要性判断——
字节相等是机器 100% 不会判断错的事，误判风险归零。
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from .config import CONFIG

_LEVEL_RE = re.compile(
    r"\b(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|PANIC)\b",
    re.IGNORECASE,
)
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2}")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")

_ELIDED_MARK = "… {n} lines elided (knowe) …"
_SCALAR_TYPES = (str, int, float, bool)

# safe_read_file 注入的行号前缀（'NNN│ ' 格式），折叠判定前剥离，输出不受影响
_LINE_NO_RE = re.compile(r"^\s*\d+\s*[│|]\s*")


def _strip_line_number_prefix(line: str) -> str:
    """剥离行号前缀（如 '  23│ ERROR x' -> 'ERROR x'），仅用于重复判定。

    无前缀的行原样返回；普通数字开头文本（无 │ 分隔符）不受影响。
    """
    return _LINE_NO_RE.sub("", line, count=1)


# ── detect：按内容形态路由 ───────────────────────────────────────


def detect(content: str) -> str:
    """返回 "json" | "log" | "plain"。任何异常 -> "plain"（fail-open 到最保守路径）。"""
    stripped = content.strip()
    if stripped[:1] in ("{", "["):
        try:
            json.loads(stripped)
            return "json"
        except Exception:
            pass  # 首字符像 JSON 但解析失败 -> 落到 log/plain 判定，不直接判 plain
    try:
        lines = content.split("\n")
        if not lines:
            return "plain"
        hits = sum(
            1
            for line in lines
            if _LEVEL_RE.search(line) or _TS_RE.search(line)
        )
        if hits * 2 >= len(lines):  # 命中行占比 >= 50%
            return "log"
    except Exception:
        pass
    return "plain"


# ── fold_repeated_lines：日志重复行折叠 ───────────────────────────


def fold_repeated_lines(text: str, run_min: int | None = None) -> tuple[str, dict]:
    """连续完全相同行 run>=3 折叠为一行标记；其余一字不动。

    - 比较按行内容（兼容 \\r\\n：比较前 rstrip 行尾 \\r，输出保留原行）
    - 折叠产物 >= 原 run 总长 -> 保留原行（诚实零）
    - 空行 run>=3 同样折叠
    - 幂等：标记行自身不是重复行，二次压缩不再折叠
    """
    run_min = run_min if run_min is not None else CONFIG.tool_compress_log_run_min
    lines = text.split("\n")
    out_lines: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        # 比较 key 剥离行号前缀（safe_read_file 'NNN│ ' 注入），输出保留原行
        key = _strip_line_number_prefix(lines[i].rstrip("\r"))
        j = i
        while j + 1 < n and _strip_line_number_prefix(lines[j + 1].rstrip("\r")) == key:
            j += 1
        run = j - i + 1
        if run >= run_min:
            marker = _ELIDED_MARK.format(n=run)
            original_len = sum(len(lines[k]) + 1 for k in range(i, j + 1))
            if len(marker) < original_len:
                out_lines.append(marker)
                i = j + 1
                continue
        out_lines.append(lines[i])
        i += 1
    out = "\n".join(out_lines)
    changed = out != text
    return out, {"method": "fold_log" if changed else "passthrough"}


# ── compact_json：JSON 无损重编码（往返一致）──────────────────────


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, _SCALAR_TYPES)


def _render_scalar(value: Any) -> str:
    """渲染单个 JSON 标量。字符串按安全去引号规则；其余用 json 语法裸写。"""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, ensure_ascii=False)
    # str：安全去引号
    s = value
    must_quote = (
        any(ch in s for ch in ',:"\n\t')
        or s != s.strip()
        or s[:1].isdigit()
        or s in ("true", "false", "null")
        or s[:1] in "[{\""
        or s.startswith("-")
    )
    if must_quote:
        return json.dumps(s, ensure_ascii=False)
    return s


def _split_values(line: str) -> list[str]:
    """引号感知切分：加引号的值可能含逗号/转义，不能裸 split(",")。"""
    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and in_quote and i + 1 < len(line):
            buf.append(ch)
            buf.append(line[i + 1])
            i += 2
            continue
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
        elif ch == "," and not in_quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _parse_scalar(token: str) -> Any:
    """decode 侧：把重编码后的单个值还原为原始 JSON 标量。"""
    if token == "null":
        return None
    if token == "true":
        return True
    if token == "false":
        return False
    if token.startswith('"'):
        return json.loads(token)
    if token[:1].isdigit() or token.startswith("-"):
        try:
            return json.loads(token)
        except ValueError:
            return token
    return token


def _compress_nested(value: Any) -> tuple[Any, bool]:
    """递归压缩 dict/list 内的大 str 字段（≥门槛）。

    [v1.0.34-实测v3] Worker 真实链路工具结果是 ToolResult.to_dict() 形状：
    {call_id, name, ok, summary, facts:{status, output:<大内容>}, ...}——
    大内容在嵌套 dict/list 里，旧实现只遍历顶层 str 字段 → facts 被跳过 → 永远透传。
    这里递归进入 dict/list，对所有 str 字段尝试内部压缩（log→折叠 / json→rows），
    返回 (新值, 是否变更)；任何一步失败保持原样（fail-closed）。
    """
    if isinstance(value, str):
        if len(value) < CONFIG.tool_compress_min_chars:
            return value, False
        kind = detect(value)
        if kind == "log":
            inner_out, inner_meta = fold_repeated_lines(value)
            if inner_meta["method"] == "fold_log":
                return inner_out, True
            return value, False
        if kind == "json":
            inner_out, inner_meta = compact_json(value)
            if inner_meta["method"] == "compact_json":
                return inner_out, True
        return value, False
    if isinstance(value, dict):
        changed = False
        new_dict: dict[Any, Any] = {}
        for k, v in value.items():
            nv, c = _compress_nested(v)
            new_dict[k] = nv
            changed = changed or c
        return new_dict, changed
    if isinstance(value, list):
        changed = False
        new_list: list[Any] = []
        for item in value:
            ni, c = _compress_nested(item)
            new_list.append(ni)
            changed = changed or c
        return new_list, changed
    return value, False


def compact_json(text: str) -> tuple[str, dict]:
    """同构对象数组 -> rows 表；标量数组 -> values 行。只在更短时生效，否则透传。

    形状 A：[{"name":"a","qty":1}, ...]  ->  rows[N]{name,qty}:\\n a,1\\n b,2
    形状 B：[1,2,3]                     ->  values[3]: 1,2,3
    其他形状 / 键名不合规 / 值非标量 -> 透传（fail-closed）。
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data:
            # [v1.0.34-实测v2/3] 工具结果包装解包：
            #   真实链路工具结果全是 {"status":"ok", field: "<内容>"}（tools_knowe._ok），
            #   首字符 { → detect 判 json → 旧实现只认数组 → 包装对象透传 → fold_log 永远闲置。
            #   v2：对顶层大字符串字段内部压缩（log→折叠 / json数组→rows 表）。
            #   v3：Worker 侧是 ToolResult.to_dict() 形状，大内容在 facts 嵌套 dict/list 里，
            #       改为递归（_compress_nested），重组后更短才生效（诚实零）；
            #       任何一步失败透传（fail-closed）。
            new_data, changed_any = _compress_nested(data)
            if changed_any:
                out = json.dumps(new_data, ensure_ascii=False)
                if len(out) < len(text):
                    return out, {"method": "compact_json"}
            return text, {"method": "passthrough"}
        if not isinstance(data, list) or not data:
            return text, {"method": "passthrough"}
        if all(isinstance(item, dict) and item for item in data):
            keys = list(data[0].keys())  # 保持原始键序（dict 保序），不用 set
            if not all(set(item.keys()) == set(keys) for item in data):
                return text, {"method": "passthrough"}
            if not all(_KEY_RE.match(k) for k in keys):
                return text, {"method": "passthrough"}
            if not all(_is_scalar(item[k]) for item in data for k in keys):
                return text, {"method": "passthrough"}
            header = f"rows[{len(data)}]{{{','.join(keys)}}}:"
            body = "\n".join(
                ",".join(_render_scalar(item[k]) for k in keys) for item in data
            )
            out = header + "\n" + body
            if len(out) >= len(text):
                return text, {"method": "passthrough"}
            return out, {"method": "compact_json"}
        if all(_is_scalar(item) for item in data):
            header = f"values[{len(data)}]:"
            out = header + " " + ",".join(_render_scalar(item) for item in data)
            if len(out) >= len(text):
                return text, {"method": "passthrough"}
            return out, {"method": "compact_json"}
    except Exception:
        pass
    return text, {"method": "passthrough"}


def decode(encoded: str) -> Any:
    """把 compact_json 产物还原为原始 JSON 数据（产品路径不调用，测试往返用）。"""
    lines = encoded.split("\n")
    header = lines[0]
    if header.startswith("rows["):
        m = re.match(r"^rows\[(\d+)\]\{([^}]*)\}:$", header)
        if not m:
            raise ValueError(f"bad rows header: {header!r}")
        keys = m.group(2).split(",")
        items = []
        for line in lines[1:]:
            if not line:
                continue
            tokens = _split_values(line)
            if len(tokens) != len(keys):
                raise ValueError(f"row arity mismatch: {line!r}")
            items.append(dict(zip(keys, (_parse_scalar(t) for t in tokens))))
        return items
    if header.startswith("values["):
        m = re.match(r"^values\[(\d+)\]: (.*)$", header)
        if not m:
            raise ValueError(f"bad values header: {header!r}")
        tokens = _split_values(m.group(2))
        return [_parse_scalar(t) for t in tokens]
    raise ValueError(f"unrecognized compact form: {header!r}")


# ── compress：唯一入口 ───────────────────────────────────────────


def compress(content: str) -> tuple[str, dict]:
    """类型化压缩入口。门槛/路由/fail-closed/诚实零 全在此收口。

    返回 (out, meta)，meta = {method, before_chars, after_chars}。
    """
    before = len(content)
    meta: dict[str, Any] = {"method": "passthrough", "before_chars": before, "after_chars": before}
    if len(content) < CONFIG.tool_compress_min_chars:
        return content, meta
    try:
        kind = detect(content)
        if kind == "json":
            out, inner = compact_json(content)
        elif kind == "log":
            out, inner = fold_repeated_lines(content)
        else:
            return content, meta
        if out == content or inner["method"] == "passthrough":
            return content, meta
        meta["method"] = inner["method"]
        meta["after_chars"] = len(out)
        return out, meta
    except Exception:
        return content, meta  # fail-closed


def compress_tool_result(content: str) -> str:
    """工具结果接入点包装：开关开时压缩，关时原文；任何异常 -> 原文。

    三处调用（agent_loop / runtime / deepseek）统一走这里：
    只改请求载体（messages）里的 content，权威历史/新消息副本仍存原文。
    实际压缩发生时记录台账（M4 统计：次数/节省字符/方法），快照见 snapshot_compression_stats。
    """
    # [v1.0.34-实测v2] 开关诊断：每次调用打印开关状态/输入大小/类型判定
    try:
        import logging as _logging
        _logging.getLogger("knowe.compress").warning(
            "[compress-debug] enabled=%s len=%d kind=%s",
            CONFIG.tool_compress_enabled, len(content),
            detect(content) if CONFIG.tool_compress_enabled else "n/a",
        )
    except Exception:
        pass
    if not CONFIG.tool_compress_enabled:
        return content
    try:
        out, meta = compress(content)
    except Exception:
        return content  # fail-closed：压缩失败路径与透传路径行为完全一致
    if out is not content and meta["method"] != "passthrough":
        _record_compression(meta["method"], meta["before_chars"], meta["after_chars"])
    return out


# ── [v1.0.34] M4 压缩台账（进程级累计，回合边界快照）──

_ledger_lock = threading.Lock()
_ledger: dict[str, Any] = {
    "count": 0,
    "saved_chars": 0,
    "by_method": {},
}


def _record_compression(method: str, before_chars: int, after_chars: int) -> None:
    with _ledger_lock:
        _ledger["count"] += 1
        _ledger["saved_chars"] += max(0, before_chars - after_chars)
        _ledger["by_method"][method] = _ledger["by_method"].get(method, 0) + 1


def snapshot_compression_stats(reset: bool = True) -> dict[str, Any]:
    """快照并（默认）清零压缩台账。返回 {count, saved_chars, by_method}。"""
    with _ledger_lock:
        snapshot = {
            "count": _ledger["count"],
            "saved_chars": _ledger["saved_chars"],
            "by_method": dict(_ledger["by_method"]),
        }
        if reset:
            _ledger["count"] = 0
            _ledger["saved_chars"] = 0
            _ledger["by_method"] = {}
        return snapshot
