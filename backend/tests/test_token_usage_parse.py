"""M1 token-usage 采集层测试：四协议解析 / usage 事件透传 / 落盘格式 / 聚合 / 价格。

运行：pytest backend/tests/test_token_usage_parse.py -q
"""

from __future__ import annotations

import json

from knowe_core.provider_client import ProviderClient, normalize_usage_buckets
from backend.token_pricing import estimate_cost, get_model_pricing
from backend.token_usage import aggregate_token_usage, normalize_token_usage_record


# ── normalize_usage_buckets：四种协议 ────────────────────────────

def test_buckets_openai_standard():
    raw = {
        "prompt_tokens": 200,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 120},
    }
    assert normalize_usage_buckets(raw) == {
        "cache_hit_input": 120,
        "cache_miss_input": 80,
        "output": 50,
    }


def test_buckets_deepseek_custom():
    raw = {
        "prompt_tokens": 300,
        "completion_tokens": 60,
        "prompt_cache_hit_tokens": 100,
        "prompt_cache_miss_tokens": 200,
    }
    assert normalize_usage_buckets(raw) == {
        "cache_hit_input": 100,
        "cache_miss_input": 200,
        "output": 60,
    }


def test_buckets_anthropic():
    raw = {
        "input_tokens": 150,
        "output_tokens": 40,
        "cache_read_input_tokens": 90,
        "cache_creation_input_tokens": 10,
    }
    assert normalize_usage_buckets(raw) == {
        "cache_hit_input": 100,   # read + creation 相加
        "cache_miss_input": 50,
        "output": 40,
    }


def test_buckets_gemini():
    raw = {
        "usageMetadata": {
            "promptTokenCount": 250,
            "candidatesTokenCount": 30,
            "cachedContentTokenCount": 70,
        }
    }
    assert normalize_usage_buckets(raw) == {
        "cache_hit_input": 70,
        "cache_miss_input": 180,
        "output": 30,
    }


def test_buckets_unknown_fields_fall_back_to_miss():
    raw = {"prompt_tokens": 100, "completion_tokens": 10}
    assert normalize_usage_buckets(raw) == {
        "cache_hit_input": 0,
        "cache_miss_input": 100,
        "output": 10,
    }


def test_buckets_garbage_returns_none():
    assert normalize_usage_buckets(None) is None
    assert normalize_usage_buckets({"foo": 1}) is None
    assert normalize_usage_buckets("not-a-dict") is None


# ── normalize_usage_buckets：容器键穿透（message/data/response 等） ──

def test_buckets_envelope_single_level():
    """Anthropic 原生流：usage 被包在 message 里。"""
    raw = {"message": {"usage": {"input_tokens": 150, "output_tokens": 40, "cache_read_input_tokens": 90}}}
    assert normalize_usage_buckets(raw) == {
        "cache_hit_input": 90,
        "cache_miss_input": 60,
        "output": 40,
    }


def test_buckets_envelope_nested():
    """网关 SDK：data -> response 两层容器。"""
    raw = {
        "data": {
            "response": {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 30},
                }
            }
        }
    }
    assert normalize_usage_buckets(raw) == {
        "cache_hit_input": 30,
        "cache_miss_input": 70,
        "output": 20,
    }


def test_buckets_envelope_priority():
    """同时存在专门键和容器键时，专门键优先（不误钻 message/data）。"""
    raw = {
        "usage": {"prompt_tokens": 200, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 120}},
        "message": {"usage": {"input_tokens": 999, "output_tokens": 999}},
    }
    assert normalize_usage_buckets(raw) == {
        "cache_hit_input": 120,
        "cache_miss_input": 80,
        "output": 50,
    }


# ── _parse_sse_line：usage 事件透传（根修验证） ───────────────────

def _parse_line(payload: dict) -> list[dict]:
    events, done = ProviderClient._parse_sse_line("data: " + json.dumps(payload))
    return events, done


def test_sse_line_emits_usage_event():
    events, done = _parse_line({
        "choices": [],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    })
    assert done is False
    assert any(e.get("type") == "usage" for e in events)
    usage_event = next(e for e in events if e.get("type") == "usage")
    assert usage_event["usage"]["prompt_tokens"] == 100


def test_sse_line_usage_frame_with_empty_choices():
    # 旧行为：choices 为空直接丢弃整帧（usage 丢失）。新行为：usage 事件必须产出。
    events, _ = _parse_line({
        "choices": [],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    })
    assert len(events) == 1
    assert events[0]["type"] == "usage"


def test_sse_line_usage_with_content_frame():
    events, _ = _parse_line({
        "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 2},
    })
    types = [e.get("type") for e in events]
    assert types == ["delta", "finish", "usage"]


def test_sse_line_no_usage_no_event():
    events, _ = _parse_line({"choices": [{"delta": {"content": "hi"}}]})
    assert all(e.get("type") != "usage" for e in events)


# ── _build_body：流式默认请求 include_usage（Kimi 实测发现） ────────

def _bare_client() -> ProviderClient:
    client = object.__new__(ProviderClient)
    client.model = "test-model"
    return client


def test_build_body_stream_defaults_include_usage():
    body = _bare_client()._build_body(
        messages=[{"role": "user", "content": "hi"}], tools=None, temperature=None,
        max_tokens=None, stream=True, extra_body=None,
    )
    assert body["stream_options"] == {"include_usage": True}


def test_build_body_extra_body_overrides_stream_options():
    body = _bare_client()._build_body(
        messages=[{"role": "user", "content": "hi"}], tools=None, temperature=None,
        max_tokens=None, stream=True,
        extra_body={"stream_options": {"include_usage": False}},
    )
    assert body["stream_options"] == {"include_usage": False}


def test_build_body_non_stream_has_no_stream_options():
    body = _bare_client()._build_body(
        messages=[{"role": "user", "content": "hi"}], tools=None, temperature=None,
        max_tokens=None, stream=False, extra_body=None,
    )
    assert "stream_options" not in body


# ── normalize_token_usage_record：新格式 + 旧格式兼容 ──────────────

def test_record_new_schema():
    row = normalize_token_usage_record({
        "ts": 1722400000,
        "project_id": "p_1",
        "agent_id": "orchestrator",
        "agent_role": "coordinator",
        "agent_name": "总管",
        "provider": "kimi",
        "model": "kimi-k2.6",
        "usage": {"cache_hit_input": 10, "cache_miss_input": 90, "output": 30},
        "price_cny": 0.0007,
    })
    assert row is not None
    assert row["ts"] == 1722400000
    assert row["agent_id"] == "orchestrator"
    assert row["usage"] == {"cache_hit_input": 10, "cache_miss_input": 90, "output": 30}
    assert row["price_cny"] == 0.0007


def test_record_legacy_schema_mapped():
    row = normalize_token_usage_record({
        "date": "2026-07-30",
        "timestamp": "2026-07-30T10:00:00+08:00",
        "agent_id": "worker_1",
        "role": "worker",
        "model": "deepseek-v4-flash",
        "input_tokens": 200,
        "output_tokens": 50,
        "total_tokens": 250,
    })
    assert row is not None
    assert row["agent_role"] == "worker"
    assert row["usage"]["cache_miss_input"] == 200
    assert row["usage"]["output"] == 50
    assert row["ts"] is not None


def test_record_invalid_returns_none():
    assert normalize_token_usage_record(None) is None
    assert normalize_token_usage_record({"agent_id": "x"}) is None  # 无 ts/无时间
    assert normalize_token_usage_record({"ts": "bad", "agent_id": "x", "model": "m"}) is None


# ── aggregate_token_usage：时间过滤 + 三桶 ────────────────────────

import time as _time

_T0 = int(_time.mktime((2026, 7, 30, 10, 0, 0, 0, 0, -1)))  # 2026-07-30 本地


def _record(ts: int, agent: str = "orchestrator", model: str = "kimi-k2.6",
            hit: int = 0, miss: int = 100, output: int = 50) -> dict:
    return {
        "ts": ts, "project_id": "p_1", "agent_id": agent,
        "agent_role": "coordinator" if agent == "orchestrator" else "worker",
        "agent_name": agent, "provider": "kimi", "model": model,
        "usage": {"cache_hit_input": hit, "cache_miss_input": miss, "output": output},
    }


def test_aggregate_time_range_filter():
    records = [_record(_T0), _record(_T0 + 3600), _record(_T0 + 7200)]
    result = aggregate_token_usage(records, start_ts=_T0 + 1800, end_ts=_T0 + 5400)
    assert result["totals"]["total_calls"] == 1
    assert result["totals"]["total_tokens"] == 150


def test_aggregate_cache_buckets():
    result = aggregate_token_usage([_record(_T0, hit=30, miss=70, output=20)])
    totals = result["totals"]
    assert totals["cache_hit_input"] == 30
    assert totals["cache_miss_input"] == 70
    assert totals["total_input"] == 100
    assert totals["total_output"] == 20
    assert result["daily"][0]["cache_hit_input"] == 30


def test_aggregate_by_agent_and_model():
    records = [
        _record(_T0, agent="orchestrator", hit=10, miss=90, output=30),
        _record(_T0 + 1, agent="worker_1", model="deepseek-v4-flash", hit=0, miss=50, output=10),
    ]
    result = aggregate_token_usage(records)
    assert len(result["by_agent"]) == 2
    assert len(result["by_model"]) == 2
    model_row = next(m for m in result["by_model"] if m["model"] == "kimi-k2.6")
    assert model_row["cache_hit_input"] == 10
    assert model_row["cache_miss_input"] == 90


def test_aggregate_uses_persisted_price_freezing_history():
    # [v1.0.34] 历史金额冻结：聚合优先用落盘价（写入时的价格），改价不重算历史
    frozen = _record(_T0, hit=1_000_000, miss=1_000_000, output=1_000_000)
    frozen["price_cny"] = 3.33   # 旧价落盘（任意值，仅验证不被当前价格表现算覆盖）
    frozen["price_usd"] = 0.5
    result = aggregate_token_usage([frozen])
    t = result["totals"]
    assert t["estimated_cost_cny"] == 3.33
    assert t["estimated_cost_usd"] == 0.5
    m = next(m for m in result["by_model"] if m["model"] == "kimi-k2.6")
    assert m["estimated_cost_cny"] == 3.33
    assert m["estimated_cost_usd"] == 0.5


def test_aggregate_fallback_to_current_price_when_no_persisted():
    # 无落盘价字段的旧格式记录 → 当前价格表现算兜底（kimi-k2.6 CNY 1.10/6.50/27.00）
    rec = _record(_T0, hit=1_000_000, miss=1_000_000, output=1_000_000)
    result = aggregate_token_usage([rec])
    t = result["totals"]
    assert t["estimated_cost_cny"] == round(1.10 + 6.50 + 27.00, 2)


# ── estimate_cost：三档单价 ───────────────────────────────────────

def test_estimate_cost_cny_three_tiers():
    # kimi-k2.6 CNY：命中 ¥1.10 / 未命中 ¥6.50 / 输出 ¥27.00（每 1M）
    cost = estimate_cost(
        "kimi-k2.6",
        cache_hit_input=1_000_000,
        cache_miss_input=1_000_000,
        output=1_000_000,
        currency="CNY",
    )
    assert cost is not None
    assert abs(cost - (1.10 + 6.50 + 27.00)) < 1e-6


def test_estimate_cost_no_cache_tier_uses_miss_rate():
    # 无缓存档模型（gpt-5.5-pro，M2 数据）：命中档缺失 → 按未命中输入价计。
    pricing = get_model_pricing("gpt-5.5-pro", "USD")
    assert pricing is not None and pricing.cache_hit_input_cost_per_1M is None
    cost = estimate_cost(
        "gpt-5.5-pro", cache_hit_input=500_000, cache_miss_input=500_000,
        output=1_000_000, currency="USD",
    )
    assert cost is not None
    assert abs(cost - (15.00 + 15.00 + 180.00)) < 1e-6


def test_estimate_cost_unpriced_returns_none():
    assert estimate_cost("no-such-model", currency="CNY") is None
    assert estimate_cost("deepseek-chat", currency="CNY") is None  # 旧别名无 CNY 价
