"""Single, non-destructive context projection for Coordinator provider calls.

Authoritative conversation history is never edited by this module.  When a provider
request needs a bounded projection, older messages are represented by stable source
references (or source ranges) and recent protocol-complete messages are carried verbatim.
The projection is explicitly labelled as non-authoritative and recoverable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from typing import Any, Iterable

__all__ = [
    "CompressConfig",
    "DEFAULT_CONFIG",
    "SUMMARY_MARK",
    "classify_layer",
    "compress_messages",
    "is_scaffolding_user",
    "project_messages",
]

SUMMARY_MARK = "【上下文投影"
_SCAFFOLD_PREFIXES = (
    "⚠【系统内部指令",
    "⚠【执行提醒",
    "⚠【上一轮事故",
)


def is_scaffolding_user(content: Any) -> bool:
    """Recognize only explicit harness control-note prefixes."""

    return isinstance(content, str) and content.lstrip().startswith(_SCAFFOLD_PREFIXES)


def classify_layer(message: dict[str, Any]) -> str:
    """Return a diagnostic class without deleting or rewriting the message."""

    role = str(message.get("role") or "")
    if role == "user":
        return "L3_system" if is_scaffolding_user(message.get("content")) else "L1_user"
    if role == "assistant":
        calls = message.get("tool_calls") or []
        names = {
            str((call.get("function") or {}).get("name") or "")
            for call in calls
            if isinstance(call, dict)
        }
        if names & {"propose_next", "propose_agents", "propose_remove_agent"}:
            return "L1_decision"
        runtime = message.get("worker_runtime")
        runtime_status = (
            str(runtime.get("completion_status") or "").upper()
            if isinstance(runtime, dict)
            else ""
        )
        if (
            message.get("completion")
            or message.get("worker_completion")
            or runtime_status in {
                "SUCCEEDED", "PARTIAL", "WAITING", "BLOCKED", "FAILED",
                "CANCELLED", "SYSTEM_ERROR", "TIMED_OUT",
            }
        ):
            return "L1_deliverable"
    return "L2_exec"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


class CompressConfig:
    """Provider projection policy.

    ``projection_chars`` is a request projection budget, not an authority-store limit.
    Legacy item-cap arguments are accepted for source compatibility and intentionally
    ignored: no category or per-item fact cap is applied.
    """

    def __init__(
        self,
        enabled: bool | None = None,
        trigger: int | None = None,
        keep_recent: int | None = None,
        projection_chars: int | None = None,
        max_user_reqs: int | None = None,
        max_decisions: int | None = None,
        max_deliverables: int | None = None,
        per_item_chars: int | None = None,
    ) -> None:
        del max_user_reqs, max_decisions, max_deliverables, per_item_chars
        raw = os.environ.get("KNOWE_CTX_COMPRESS", "1").strip().lower()
        self.enabled = raw not in {"0", "false", "off", "no"} if enabled is None else bool(enabled)
        self.trigger = max(1, _env_int("KNOWE_CTX_TRIGGER", 60) if trigger is None else int(trigger))
        self.keep_recent = max(
            1,
            _env_int("KNOWE_CTX_KEEP_RECENT", 30)
            if keep_recent is None
            else int(keep_recent),
        )
        self.projection_chars = max(
            4_096,
            _env_int("KNOWE_CTX_PROJECTION_CHARS", 180_000)
            if projection_chars is None
            else int(projection_chars),
        )


DEFAULT_CONFIG = CompressConfig()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _message_chars(message: dict[str, Any]) -> int:
    return len(_canonical(message))


# ── [v1.0.34] M3 预算 token 化：本地 token 估算（不依赖供应商接口）──

# 正则（C 实现）计数 CJK，避免逐字符 Python 循环：200 万字符消息的
# _estimate_tokens 从 ~4s 降到 ~0.01s（cProfile 实测 2026-08-13）。
_CJK_RE = re.compile(
    "["
    "\u4e00-\u9fff"      # 基本汉字
    "\u3400-\u4dbf"      # 扩展 A
    "\uf900-\ufaff"      # 兼容汉字
    "\U00020000-\U0002fa1f"  # 扩展 B 起
    "]"
)


def _is_cjk(ch: str) -> bool:
    return _CJK_RE.match(ch) is not None


def _estimate_tokens(text: str) -> int:
    """本地近似 token 数：中文 1 字 ≈ 0.7 token、英文 4 字符 ≈ 1 token。

    确定性、离线；只用于预算裁决与兜底判断，不参与任何发送内容。
    """
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return int(cjk * 0.7 + other / 4)


def _tokenize(text: str) -> list[str]:
    """BM25 分词：英文/数字按连续段切分（小写）；中文按单字（unigram）。

    中文无空格边界，连续 CJK 段做整词会导致 query 词永远匹配不上文档词
    （实测教训 2026-08-13）。单字分词是中文 BM25 的标准做法，确定性、无依赖。
    """
    tokens: list[str] = []
    current: list[str] = []
    for ch in text.lower():
        if _is_cjk(ch):
            tokens.append(ch)  # 每个汉字独立成词
        elif ch.isalnum():
            current.append(ch)
        else:
            if current:
                tokens.append("".join(current))
                current.clear()
    if current:
        tokens.append("".join(current))
    return tokens


def _bm25_scores(messages: list[dict[str, Any]], query: str) -> list[float]:
    """BM25 打分（k1=1.5, b=0.75，局部 IDF，确定性）。

    分数只用于预算内「谁拿 full payload」的优先级，不改输出顺序。
    """
    k1, b = 1.5, 0.75
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return [0.0] * len(messages)
    docs = [_tokenize(_canonical(message)) for message in messages]
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / n if n else 0.0
    df = {t: sum(1 for d in docs if t in d) for t in q_tokens}
    scores: list[float] = []
    for doc in docs:
        dl = len(doc)
        total = 0.0
        for term in q_tokens:
            if term not in doc:
                continue
            tf = doc.count(term)
            idf = math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denom = tf + k1 * (1.0 - b + b * dl / avgdl) if avgdl else tf + k1
            total += idf * (tf * (k1 + 1.0)) / denom
        scores.append(total)
    return scores


def _message_ref(index: int) -> str:
    return f"conversation://message/{index + 1}"


def _range_ref(start: int, end: int) -> str:
    return f"conversation://messages/{start + 1}-{end + 1}"


def _clean_protocol_boundary(messages: list[dict[str, Any]], proposed: int) -> int:
    """Move a tail cut to a user boundary so tool_call/result pairs stay intact."""

    if proposed <= 0:
        return 0
    for index in range(proposed, len(messages)):
        if messages[index].get("role") == "user":
            return index
    for index in range(proposed - 1, -1, -1):
        if messages[index].get("role") == "user":
            return index
    return len(messages)


def _tail_start(messages: list[dict[str, Any]], cfg: CompressConfig) -> int:
    proposed = max(0, len(messages) - cfg.keep_recent)
    start = _clean_protocol_boundary(messages, proposed)

    # The recent verbatim tail is itself a projection.  If it exceeds its share, move the
    # cut forward by whole user turns; the omitted turn remains represented by refs.
    tail_budget = int(cfg.projection_chars * 0.68)
    while start < len(messages) and sum(_message_chars(m) for m in messages[start:]) > tail_budget:
        next_user = next(
            (i for i in range(start + 1, len(messages)) if messages[i].get("role") == "user"),
            None,
        )
        if next_user is None:
            break
        start = next_user
    return start


def _entry_text(message: dict[str, Any], index: int, *, include_payload: bool) -> str:
    role = str(message.get("role") or "unknown")
    ref = _message_ref(index)
    digest = _sha(message)
    chars = _message_chars(message)
    header = f"- source_ref={ref}; role={role}; chars={chars}; sha256={digest}"
    if not include_payload:
        return header + "; payload=authoritative-history"
    return header + "\n  payload=" + _canonical(message)


def _projection_index(
    messages: list[dict[str, Any]],
    end: int,
    budget: int,
    query: str | None = None,
) -> str:
    """Render recoverable refs for every older message, coalescing only as ranges.

    [v1.0.34] query 非空时按 BM25 打分：预算内优先保留高分消息的全 payload，
    低分消息降级为 descriptor 引用；输出保持原顺序（时间线语义不破坏）。
    query 为空时与 v1.0.33 行为逐字节一致（原顺序 full→descriptor→range）。
    """

    if end <= 0:
        return ""

    # ── 无 query：原逻辑逐字节保持（full 放不下立即退化 descriptor 并占用预算）──
    if not query:
        lines: list[str] = []
        used = 0
        omitted_range_start: int | None = None
        for index, message in enumerate(messages[:end]):
            full = _entry_text(message, index, include_payload=True)
            descriptor = _entry_text(message, index, include_payload=False)
            candidate = full if used + len(full) + 1 <= budget else descriptor
            if used + len(candidate) + 1 <= budget:
                if omitted_range_start is not None:
                    range_end = index - 1
                    range_line = (
                        f"- source_range={_range_ref(omitted_range_start, range_end)}; "
                        f"count={range_end - omitted_range_start + 1}; "
                        f"sha256={_sha(messages[omitted_range_start:index])}; "
                        "payload=authoritative-history"
                    )
                    lines.append(range_line)
                    used += len(range_line) + 1
                    omitted_range_start = None
                lines.append(candidate)
                used += len(candidate) + 1
            elif omitted_range_start is None:
                omitted_range_start = index
        if omitted_range_start is not None:
            range_end = end - 1
            lines.append(
                f"- source_range={_range_ref(omitted_range_start, range_end)}; "
                f"count={range_end - omitted_range_start + 1}; "
                f"sha256={_sha(messages[omitted_range_start:end])}; "
                "payload=authoritative-history"
            )
        return "\n".join(lines)

    # ── 有 query：BM25 高分优先 full payload，低分降级 descriptor，按原序输出 ──
    scores = _bm25_scores(messages[:end], query)
    order = list(range(end))
    order.sort(key=lambda i: scores[i], reverse=True)
    full_flags = [False] * end
    used = 0
    for index in order:
        full = _entry_text(messages[index], index, include_payload=True)
        if used + len(full) + 1 <= budget:
            full_flags[index] = True
            used += len(full) + 1

    lines = []
    used = 0
    omitted_range_start = None
    for index, message in enumerate(messages[:end]):
        candidate = _entry_text(message, index, include_payload=full_flags[index])
        if used + len(candidate) + 1 <= budget:
            if omitted_range_start is not None:
                range_end = index - 1
                range_line = (
                    f"- source_range={_range_ref(omitted_range_start, range_end)}; "
                    f"count={range_end - omitted_range_start + 1}; "
                    f"sha256={_sha(messages[omitted_range_start:index])}; "
                    "payload=authoritative-history"
                )
                lines.append(range_line)
                used += len(range_line) + 1
                omitted_range_start = None
            lines.append(candidate)
            used += len(candidate) + 1
        elif omitted_range_start is None:
            omitted_range_start = index

    if omitted_range_start is not None:
        range_end = end - 1
        lines.append(
            f"- source_range={_range_ref(omitted_range_start, range_end)}; "
            f"count={range_end - omitted_range_start + 1}; "
            f"sha256={_sha(messages[omitted_range_start:end])}; "
            "payload=authoritative-history"
        )
    return "\n".join(lines)


def _summary_message(
    messages: list[dict[str, Any]],
    head_end: int,
    cfg: CompressConfig,
    query: str | None = None,
) -> dict[str, Any]:
    from .i18n_backend import msg  # 局部导入：模块级 msg() 会固化语言
    header = (
        f"{msg('ctx.001')}{msg('ctx.002')}\n"
        f"projected_source_range={_range_ref(0, head_end - 1)}\n"
        f"projected_messages={head_end}; authoritative_messages={len(messages)}\n"
        + msg("ctx.003")
        + "\n"
        + msg("ctx.004")
        + "\n"
    )
    index_budget = max(512, int(cfg.projection_chars * 0.30) - len(header))
    content = header + _projection_index(messages, head_end, index_budget, query=query)
    return {
        "role": "user",
        "content": content,
        "metadata": {
            "projection": True,
            "authoritative": False,
            "source_ref": _range_ref(0, head_end - 1),
            "source_start": 1,
            "source_end": head_end,
            "source_count": head_end,
            "authoritative_count": len(messages),
        },
    }


def _token_budget(cfg: CompressConfig) -> int:
    """投影预算（token 估算）：KNOWE_MODEL_CONTEXT_WINDOW × 0.8 安全边际。

    预留系统提示/工具描述/当前轮/输出余量。测试可经环境变量收紧窗口。
    """
    from .config import CONFIG
    window = CONFIG.model_context_window
    return max(1, int(window * 0.8))


def _projected_token_estimate(messages: list[dict[str, Any]]) -> int:
    return _estimate_tokens(_canonical(messages))


def project_messages(
    messages: list[dict[str, Any]],
    cfg: CompressConfig | None = None,
    query: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return a provider projection and count of messages represented by refs.

    The input list and every contained message remain untouched.  A return count of zero
    means the full sequence is already within the configured projection conditions.

    [v1.0.34] query：当前最新用户消息文本（截断 16384 字符）。非空时索引区按
    BM25 高分优先保留 full payload；预算 token 化（窗口 × 0.8 上限）；超上限
    兜底：先收窄 keep_recent，再压缩索引区，仍超则明确报错（不静默发送）。
    query 为 None/空串时行为与 v1.0.33 完全一致。
    """

    cfg = cfg or DEFAULT_CONFIG
    source = [copy.deepcopy(m) for m in messages if isinstance(m, dict)]
    if not cfg.enabled:
        return source, 0
    total_chars = sum(_message_chars(message) for message in source)
    if len(source) <= cfg.trigger and total_chars <= cfg.projection_chars:
        return source, 0

    query = (query or "").strip()[:16384] or None

    start = _tail_start(source, cfg)
    if start <= 0:
        # One exceptionally large turn has no safe protocol boundary.  Represent the full
        # range by refs rather than slicing ordinary language inside that turn.
        start = len(source)
    summary = _summary_message(source, start, cfg, query=query)
    tail = source[start:]
    projected = [summary, *tail]
    represented = start

    if query is not None:
        projected, represented = _enforce_token_budget(
            source, cfg, query, projected, represented
        )
    return projected, represented


def _enforce_token_budget(
    source: list[dict[str, Any]],
    cfg: CompressConfig,
    query: str,
    projected: list[dict[str, Any]],
    represented: int,
) -> tuple[list[dict[str, Any]], int]:
    """超窗兜底：投影产物估算 token 仍超上限时逐级裁剪，仍超则明确报错。

    裁剪顺序（由轻到重）：
      1. 收窄 keep_recent（尾部 verbatim 减到 1 条 user 轮）
      2. 压缩索引区：全量 descriptor（无 full payload）
      3. 仍超 -> 抛错，不静默发送
    """
    budget = _token_budget(cfg)
    if _projected_token_estimate(projected) <= budget:
        return projected, represented

    # 1) 收窄 keep_recent：从 cfg.keep_recent 递减到 1
    for keep in range(max(1, cfg.keep_recent - 1), 0, -1):
        narrowed = CompressConfig(
            enabled=cfg.enabled,
            trigger=cfg.trigger,
            keep_recent=keep,
            projection_chars=cfg.projection_chars,
        )
        start = _tail_start(source, narrowed)
        if start <= 0:
            start = len(source)
        candidate = [_summary_message(source, start, narrowed, query=query), *source[start:]]
        if _projected_token_estimate(candidate) <= budget:
            return candidate, start

    # 2) 索引区全量 descriptor：把 full payload 全部降级（无 query 感知保留）
    start = _tail_start(source, cfg)
    if start <= 0:
        start = len(source)
    summary = _summary_message(source, start, cfg, query=query)
    content = summary["content"]
    summary["content"] = _strip_full_payloads(content)
    candidate = [summary, *source[start:]]
    if _projected_token_estimate(candidate) <= budget:
        return candidate, start

    raise RuntimeError(
        "context projection still exceeds model window budget after trimming "
        f"(estimated {_projected_token_estimate(candidate)} > {budget} tokens); "
        "refusing to send an oversized request"
    )


def _strip_full_payloads(index_text: str) -> str:
    """把投影索引里的 payload= 行全部降级为 descriptor（保留引用与 sha256）。"""
    kept: list[str] = []
    for line in index_text.split("\n"):
        if line.startswith("  payload="):
            continue
        if line.startswith("- source_ref=") and "payload=authoritative-history" not in line:
            # full 行：剥掉 payload 子行，保留 header 行（payload= 行在下一行被跳过）
            kept.append(line)
            continue
        kept.append(line)
    return "\n".join(kept)


def compress_messages(
    messages: list[dict[str, Any]],
    cfg: CompressConfig | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Backward-compatible alias for the single projection entry point."""

    return project_messages(messages, cfg)
