"""Low-cost token accounting helpers for provider calls and project summaries.

This module never participates in model-control decisions.  It only normalizes usage after
provider events already exist, and all callers are expected to treat failures as debug-only
telemetry loss rather than an agent error.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from .token_pricing import estimate_cost, estimate_cost_usd, pricing_payload
from knowe_core.provider_client import normalize_usage_buckets

log = logging.getLogger("knowe.token_usage")

_INPUT_KEYS = (
    "input_tokens", "prompt_tokens", "inputTokens",
    "prompt_token_count", "promptTokenCount", "input_token_count",
)
_OUTPUT_KEYS = (
    "output_tokens", "completion_tokens", "outputTokens",
    "candidates_token_count", "candidatesTokenCount", "output_token_count",
)
_TOTAL_KEYS = ("total_tokens", "totalTokens", "total_token_count", "totalTokenCount")
_USAGE_KEYS = ("usage", "token_usage", "usage_metadata", "usageMetadata")
_CONTAINER_KEYS = (
    "response", "data", "result", "raw", "meta", "metadata",
    # Anthropic-style stream start events carry usage under ``message``; a few adapters
    # wrap terminal SDK objects under ``chunk``/``delta``.
    "message", "chunk", "delta",
)
_MAX_USAGE_SEARCH_DEPTH = 5


def _field(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    try:
        return getattr(value, key)
    except Exception:  # telemetry introspection must never escape
        return None


def _token_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        return None
    return int(numeric)


def _first_token_int(value: Any, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        parsed = _token_int(_field(value, key))
        if parsed is not None:
            return parsed
    return None


def _usage_from_candidate(candidate: Any) -> dict[str, int] | None:
    input_tokens = _first_token_int(candidate, _INPUT_KEYS)
    output_tokens = _first_token_int(candidate, _OUTPUT_KEYS)
    total_tokens = _first_token_int(candidate, _TOTAL_KEYS)

    # Both sides are needed for a trustworthy cost split.  A total-only payload is skipped
    # rather than guessed into input/output buckets.
    if input_tokens is None or output_tokens is None:
        return None
    computed_total = input_tokens + output_tokens
    if total_tokens is not None and total_tokens != computed_total:
        log.debug(
            "provider usage total mismatch: reported=%s computed=%s; using computed total",
            total_tokens,
            computed_total,
        )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": computed_total,
    }


def _usage_parts_from_candidate(candidate: Any) -> dict[str, int] | None:
    """Read any valid usage fields from one SDK object, including split stream events."""
    parts: dict[str, int] = {}
    input_tokens = _first_token_int(candidate, _INPUT_KEYS)
    output_tokens = _first_token_int(candidate, _OUTPUT_KEYS)
    total_tokens = _first_token_int(candidate, _TOTAL_KEYS)
    if input_tokens is not None:
        parts["input_tokens"] = input_tokens
    if output_tokens is not None:
        parts["output_tokens"] = output_tokens
    if total_tokens is not None:
        parts["total_tokens"] = total_tokens
    return parts or None


def extract_token_usage_parts(event: Any) -> dict[str, int] | None:
    """Find the richest usage fragment in one provider event/response.

    Some streaming SDKs report input and output counters in different terminal events.  The
    provider proxy merges these fragments across one ``chat_stream`` call; this function only
    extracts one event and never invents a missing side.
    """
    queue: list[tuple[Any, int]] = [(event, 0)]
    seen: set[int] = set()
    best: dict[str, int] | None = None
    while queue:
        candidate, depth = queue.pop(0)
        if candidate is None:
            continue
        identity = id(candidate)
        if identity in seen:
            continue
        seen.add(identity)

        parts = _usage_parts_from_candidate(candidate)
        if parts is not None:
            if "input_tokens" in parts and "output_tokens" in parts:
                return parts
            if best is None or len(parts) > len(best):
                best = parts
        if depth >= _MAX_USAGE_SEARCH_DEPTH:
            continue

        for key in (*_USAGE_KEYS, *_CONTAINER_KEYS):
            child = _field(candidate, key)
            if child is not None and child is not candidate:
                queue.append((child, depth + 1))
    return best


def extract_token_usage(event: Any) -> dict[str, int] | None:
    """Find complete normalized usage without assuming one SDK object shape."""
    parts = extract_token_usage_parts(event)
    return _usage_from_candidate(parts) if parts is not None else None


def timestamp_to_seconds(value: Any = None) -> str:
    """Return a local, timezone-aware ISO timestamp with second precision."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.now().astimezone()
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")


class TokenUsageCollector:
    """Per-``run_conversation`` accumulator; one entry per real provider stream."""

    def __init__(self) -> None:
        self._calls: list[dict[str, Any]] = []

    def add(self, usage: Mapping[str, Any], *, timestamp: str | None = None) -> bool:
        normalized = _usage_from_candidate(usage)
        if normalized is None:
            return False
        buckets = normalize_usage_buckets(usage)
        row: dict[str, Any] = {
            **normalized,
            "timestamp": timestamp or timestamp_to_seconds(),
        }
        if buckets is not None:
            row.update(buckets)
        self._calls.append(row)
        return True

    @property
    def has_calls(self) -> bool:
        return bool(self._calls)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._calls]

    def result_fields(self, model: str) -> dict[str, Any]:
        input_tokens = sum(int(row["input_tokens"]) for row in self._calls)
        output_tokens = sum(int(row["output_tokens"]) for row in self._calls)
        return {
            "_token_usage_calls": self.calls,
            "_token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "calls": len(self._calls),
            },
            "_token_usage_model": model,
        }


def normalize_token_usage_record(raw: Any) -> dict[str, Any] | None:
    """Validate one persisted JSONL row and return its stable schema.

    New schema (v1.0.20.1): ``ts/agent_id/agent_role/agent_name/provider/model/usage``
    where ``usage`` is ``{cache_hit_input, cache_miss_input, output}``.
    Legacy rows (``date/role/input_tokens/output_tokens/total_tokens/timestamp``)
    are still accepted and mapped onto the new schema.
    """
    if not isinstance(raw, Mapping):
        return None

    ts: int | None = None
    try:
        ts_value = raw.get("ts")
        if ts_value is not None:
            ts = int(ts_value)
    except (TypeError, ValueError, OverflowError):
        ts = None
    if ts is None:
        # Legacy rows carry an ISO timestamp string instead of an epoch value.
        timestamp = str(raw.get("timestamp") or "").strip()
        if timestamp:
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.astimezone()
                ts = int(parsed.timestamp())
            except (TypeError, ValueError, OverflowError):
                ts = None
        else:
            date = str(raw.get("date") or "").strip()
            if len(date) == 10 and date[4:5] == "-" and date[7:8] == "-":
                try:
                    parsed = datetime.strptime(date, "%Y-%m-%d").astimezone()
                    ts = int(parsed.timestamp())
                except ValueError:
                    ts = None
    if ts is None:
        return None

    agent_id = str(raw.get("agent_id") or "").strip()
    if not agent_id:
        return None
    role = str(raw.get("agent_role") or raw.get("role") or "").strip()
    model = str(raw.get("model") or "").strip()
    if not model:
        return None

    # Buckets: new nested ``usage`` wins; legacy flat input/output is converted.
    nested = raw.get("usage")
    if isinstance(nested, Mapping) and (
        "cache_hit_input" in nested or "cache_miss_input" in nested or "output" in nested
    ):
        try:
            hit = int(nested.get("cache_hit_input") or 0)
            miss = int(nested.get("cache_miss_input") or 0)
            output = int(nested.get("output") or 0)
        except (TypeError, ValueError, OverflowError):
            return None
    else:
        try:
            input_total = int(raw.get("input_tokens") or 0)
            output = int(raw.get("output_tokens") or 0)
        except (TypeError, ValueError, OverflowError):
            return None
        hit = int(raw.get("cache_hit_input") or 0)
        miss = max(0, input_total - hit)

    record: dict[str, Any] = {
        "ts": ts,
        "agent_id": agent_id,
        "agent_role": role,
        "agent_name": str(raw.get("agent_name") or "").strip(),
        "provider": str(raw.get("provider") or "").strip(),
        "model": model,
        "usage": {
            "cache_hit_input": hit,
            "cache_miss_input": miss,
            "output": output,
        },
    }
    project_id = str(raw.get("project_id") or "").strip()
    conversation_id = str(raw.get("conversation_id") or "").strip()
    if project_id:
        record["project_id"] = project_id
    if conversation_id:
        record["conversation_id"] = conversation_id
    # [v1.0.34-M4] 压缩台账：count / saved_chars / by_method（可选，缺失=无压缩）
    compression = raw.get("compression")
    if isinstance(compression, Mapping) and (
        "count" in compression or "saved_chars" in compression
    ):
        try:
            comp_count = int(compression.get("count") or 0)
            comp_saved = int(compression.get("saved_chars") or 0)
        except (TypeError, ValueError, OverflowError):
            comp_count = comp_saved = 0
        comp_method = compression.get("by_method")
        record["compression"] = {
            "count": max(0, comp_count),
            "saved_chars": max(0, comp_saved),
            "by_method": (
                {str(k): int(v) for k, v in comp_method.items()}
                if isinstance(comp_method, Mapping)
                else {}
            ),
        }
    # [v1.0.34-M4] 上下文占用百分比（投影后估算 token ÷ 窗口；可选）
    pct = raw.get("context_usage_pct")
    if pct is not None:
        try:
            numeric = float(pct)
        except (TypeError, ValueError, OverflowError):
            numeric = -1.0
        if math.isfinite(numeric) and 0 <= numeric <= 100:
            record["context_usage_pct"] = round(numeric, 2)
    # [v1.0.34-M4-v2] 投影保留条数（本回合投影后保留的历史消息条数；可选）
    projected = raw.get("projected_message_count")
    if projected is not None:
        try:
            projected_n = int(projected)
        except (TypeError, ValueError, OverflowError):
            projected_n = -1
        if projected_n > 0:
            record["projected_message_count"] = projected_n
    for price_key in ("price_cny", "price_usd"):
        price_value = raw.get(price_key)
        if price_value is None:
            continue
        try:
            numeric = float(price_value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(numeric):
            record[price_key] = round(numeric, 8)
    return record


def _zero_bucket() -> dict[str, int]:
    return {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "cache_hit_input": 0, "cache_miss_input": 0, "calls": 0,
    }


def _ts_to_date(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts).astimezone().date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def aggregate_token_usage(
    records: Iterable[Mapping[str, Any]],
    *,
    names: Mapping[str, str] | None = None,
    name_lookup: Callable[[str, str], str] | None = None,
    current_model: str = "",
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> dict[str, Any]:
    """Build the WebSocket response payload's daily/agent/model/cost projections.

    ``start_ts``/``end_ts`` are inclusive epoch-second boundaries; ``None`` means
    unbounded on that side.
    """
    clean: list[dict[str, Any]] = []
    for raw in records:
        row = normalize_token_usage_record(raw)
        if row is None:
            continue
        ts = int(row["ts"])
        if start_ts is not None and ts < start_ts:
            continue
        if end_ts is not None and ts > end_ts:
            continue
        clean.append(row)

    daily_map: dict[str, dict[str, int]] = defaultdict(_zero_bucket)
    agent_map: dict[str, dict[str, Any]] = {}
    model_map: dict[str, dict[str, Any]] = {}
    total_input = 0
    total_output = 0
    total_cache_hit = 0
    total_cache_miss = 0
    priced_cost_cny = 0.0
    priced_cost_usd = 0.0
    unpriced_tokens = 0
    # [v1.0.34-M4] 上下文占用三数：压缩次数/节省字符累计，占用率取范围内最新一条
    total_compression_count = 0
    total_saved_chars = 0
    compression_by_method: dict[str, int] = defaultdict(int)
    # [v1.0.34-M4-v2] 瞬时组（本回合=范围内最新一条含数据的记录）+ 累计投影条数
    total_projected_count = 0
    latest_compression_count: int | None = None
    latest_saved_chars: int | None = None
    latest_projected_count: int | None = None
    latest_context_usage_pct: float | None = None

    for row in clean:
        usage = row["usage"]
        hit = int(usage["cache_hit_input"])
        miss = int(usage["cache_miss_input"])
        output = int(usage["output"])
        input_tokens = hit + miss
        total_tokens = input_tokens + output
        total_input += input_tokens
        total_output += output
        total_cache_hit += hit
        total_cache_miss += miss

        # [v1.0.34-M4] 三数累计（row 为范围过滤后的有序流，ts 递增）
        compression = row.get("compression")
        if compression:
            total_compression_count += int(compression.get("count") or 0)
            total_saved_chars += int(compression.get("saved_chars") or 0)
            for method, method_count in (compression.get("by_method") or {}).items():
                compression_by_method[str(method)] += int(method_count or 0)
            # [v1.0.34-M4-v2] 瞬时组：本回合 = 最新一条含压缩数据的记录
            latest_compression_count = int(compression.get("count") or 0)
            latest_saved_chars = int(compression.get("saved_chars") or 0)
        # [v1.0.34-M4-v2] 投影保留条数：累计 + 瞬时（最新一条含投影数据的记录）
        if "projected_message_count" in row:
            total_projected_count += int(row["projected_message_count"] or 0)
            latest_projected_count = int(row["projected_message_count"] or 0)
        if "context_usage_pct" in row:
            latest_context_usage_pct = float(row["context_usage_pct"])

        day = daily_map[_ts_to_date(int(row["ts"]))]
        day["input_tokens"] += input_tokens
        day["output_tokens"] += output
        day["total_tokens"] += total_tokens
        day["cache_hit_input"] += hit
        day["cache_miss_input"] += miss
        day["calls"] += 1

        agent_id = row["agent_id"]
        role = row["agent_role"]
        agent = agent_map.setdefault(agent_id, {
            "agent_id": agent_id,
            "role": role,
            "name": "",
            "agent_name": "",
            "total_input": 0,
            "total_output": 0,
            "total_tokens": 0,
            "cache_hit_input": 0,
            "cache_miss_input": 0,
            "calls": 0,
            # [v1.0.20.3] 按 Agent 金额：每次调用按 model 查价累加（与 totals 同口径）
            "estimated_cost_cny": 0.0,
            "estimated_cost_usd": 0.0,
            "unpriced_tokens": 0,
        })
        agent["role"] = role
        if row.get("agent_name"):
            agent["agent_name"] = str(row["agent_name"]).strip()
        agent["total_input"] += input_tokens
        agent["total_output"] += output
        agent["total_tokens"] += total_tokens
        agent["cache_hit_input"] += hit
        agent["cache_miss_input"] += miss
        agent["calls"] += 1

        model_name = row["model"]
        provider = str(row.get("provider") or "").strip() or "unknown"
        model_key = f"{provider}\u0000{model_name}"
        model = model_map.setdefault(model_key, {
            "provider": provider,
            "model": model_name,
            "total_input": 0,
            "total_output": 0,
            "total_tokens": 0,
            "cache_hit_input": 0,
            "cache_miss_input": 0,
            "calls": 0,
            # [v1.0.34] 金额=行级落盘价累加（历史冻结），非总量按当前价现算
            "estimated_cost_cny": 0.0,
            "estimated_cost_usd": 0.0,
            "unpriced_tokens": 0,
        })
        model["total_input"] += input_tokens
        model["total_output"] += output
        model["total_tokens"] += total_tokens
        model["cache_hit_input"] += hit
        model["cache_miss_input"] += miss
        model["calls"] += 1

        # [v1.0.34] 金额优先用落盘价（记录写入时的价格，历史冻结）；旧记录无
        # price_* 字段才用当前价格表现算兜底——改价只影响新产生的 token。
        cny_cost = row.get("price_cny")
        usd_cost = row.get("price_usd")
        if cny_cost is None:
            cny_cost = estimate_cost(
                model_name, cache_hit_input=hit, cache_miss_input=miss, output=output,
                currency="CNY", provider=provider,
            )
        if usd_cost is None:
            usd_cost = estimate_cost(
                model_name, cache_hit_input=hit, cache_miss_input=miss, output=output,
                currency="USD", provider=provider,
            )
        if cny_cost is None and usd_cost is None:
            unpriced_tokens += total_tokens
            agent["unpriced_tokens"] += total_tokens
            model["unpriced_tokens"] += total_tokens
        else:
            if cny_cost is not None:
                priced_cost_cny += cny_cost
                agent["estimated_cost_cny"] += cny_cost
                model["estimated_cost_cny"] += cny_cost
            if usd_cost is not None:
                priced_cost_usd += usd_cost
                agent["estimated_cost_usd"] += usd_cost
                model["estimated_cost_usd"] += usd_cost

    for agent in agent_map.values():
        agent_id = str(agent["agent_id"])
        role = str(agent["role"])
        resolved = (names or {}).get(agent_id, "")
        if not resolved and name_lookup is not None:
            try:
                resolved = str(name_lookup(agent_id, role) or "")
            except Exception:
                log.debug("token usage name lookup failed for %s", agent_id, exc_info=True)
        if not resolved:
            resolved = str(agent.get("agent_name") or "").strip()
        if not resolved:
            from .i18n_backend import msg  # 局部导入：避免模块级语言固化
            resolved = msg("token.001") if agent_id == "coordinator" else (role or agent_id)
        agent["name"] = resolved
        # [v1.0.36] 按 Agent 金额终值化：有价部分照常累加显示，无价（自定义）部分不统计——
        # 不再因用过无价模型整体置 None；「部分无价」由 unpriced_tokens 字段供前端标注。
        agent["estimated_cost_cny"] = round(agent["estimated_cost_cny"], 2)
        agent["estimated_cost_usd"] = round(agent["estimated_cost_usd"], 2)

    for model in model_map.values():
        model_name = str(model["model"])
        # [v1.0.34] model 金额 = 行级落盘价累加（与 agent/totals 同口径，历史冻结）
        model["estimated_cost_cny"] = (
            None if model["unpriced_tokens"] > 0 else round(model["estimated_cost_cny"], 2)
        )
        model["estimated_cost_usd"] = (
            None if model["unpriced_tokens"] > 0 else round(model["estimated_cost_usd"], 2)
        )
        model["pricing"] = pricing_payload(model_name, provider=model["provider"])

    total_tokens = total_input + total_output
    total_calls = len(clean)
    estimated_cost_cny = round(priced_cost_cny, 2)
    estimated_cost_usd = round(priced_cost_usd, 2)

    if not current_model and clean:
        current_model = str(clean[-1]["model"])
    # [customAPI] 反查当前模型的 provider，用于价格匹配区分（custom 不匹配官方价）。
    current_provider = ""
    if current_model:
        for _row in reversed(clean):
            if str(_row.get("model") or "") == current_model:
                current_provider = str(_row.get("provider") or "").strip() or "unknown"
                break

    daily = [
        {"date": date, **bucket}
        for date, bucket in sorted(daily_map.items(), key=lambda item: item[0])
    ]
    by_agent = sorted(
        agent_map.values(),
        key=lambda row: (-int(row["total_tokens"]), str(row["name"]), str(row["agent_id"])),
    )
    by_model = sorted(
        model_map.values(),
        key=lambda row: (-int(row["total_tokens"]), str(row["model"])),
    )

    return {
        "daily": daily,
        "totals": {
            "total_input": total_input,
            "total_output": total_output,
            "total_tokens": total_tokens,
            "total_calls": total_calls,
            "cache_hit_input": total_cache_hit,
            "cache_miss_input": total_cache_miss,
            "estimated_cost_cny": estimated_cost_cny,
            "estimated_cost_usd": estimated_cost_usd,
            "priced_cost_cny": round(priced_cost_cny, 2),
            "priced_cost_usd": round(priced_cost_usd, 2),
            "cost_complete": unpriced_tokens == 0,
            "unpriced_tokens": unpriced_tokens,
            # [v1.0.34-M4] 上下文占用三数（累计）
            "compression_count": total_compression_count,
            "saved_chars": total_saved_chars,
            "compression_by_method": dict(compression_by_method),
            "context_usage_pct": latest_context_usage_pct,
            # [v1.0.34-M4-v2] 瞬时组（本回合=范围内最新一条含数据的记录）
            "latest_compression_count": latest_compression_count,
            "latest_saved_chars": latest_saved_chars,
            "latest_projected_count": latest_projected_count,
            # [v1.0.34-M4-v2] 投影保留条数累计
            "projected_count": total_projected_count,
        },
        "by_agent": by_agent,
        "by_model": by_model,
        "current_model": current_model,
        "pricing": pricing_payload(current_model, provider=current_provider),
    }
