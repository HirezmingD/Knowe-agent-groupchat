"""v1.0.34 步骤 5 — M4 token 统计扩展（后端部分）。

- 压缩台账：compress_tool_result 实际压缩时记录，快照/清零
- normalize_token_usage_record：接受 compression / context_usage_pct 字段
- aggregate_token_usage：totals 汇总三数（compression_count/saved_chars/context_usage_pct）
"""

from __future__ import annotations

import pytest

from backend import content_compress as ccom
from backend.config import CONFIG
from backend.token_usage import aggregate_token_usage, normalize_token_usage_record


# ── 压缩台账 ────────────────────────────────────────────────────


def test_ledger_records_and_snapshots() -> None:
    ccom.snapshot_compression_stats(reset=True)  # 清干净
    object.__setattr__(CONFIG, "tool_compress_enabled", True)
    try:
        log_text = ("ERROR retry\n" * 300) + "INFO done\n"  # 过 2000 门槛
        out1 = ccom.compress_tool_result(log_text)
        assert "lines elided" in out1
        json_text = __import__("json").dumps([{"name": "a", "qty": 1}] * 200)
        ccom.compress_tool_result(json_text)
        ccom.compress_tool_result("short")  # 低于门槛，不记录
    finally:
        object.__setattr__(CONFIG, "tool_compress_enabled", False)

    stats = ccom.snapshot_compression_stats(reset=True)
    assert stats["count"] == 2
    assert stats["saved_chars"] > 0
    assert stats["by_method"].get("fold_log", 0) == 1
    assert stats["by_method"].get("compact_json", 0) == 1
    # 快照后已清零
    assert ccom.snapshot_compression_stats(reset=False)["count"] == 0


def test_ledger_ignored_when_switch_off() -> None:
    ccom.snapshot_compression_stats(reset=True)
    assert CONFIG.tool_compress_enabled is False
    log_text = ("ERROR retry\n" * 300) + "INFO done\n"
    out = ccom.compress_tool_result(log_text)
    assert out == log_text
    assert ccom.snapshot_compression_stats(reset=False)["count"] == 0


# ── normalize：接受新字段 ───────────────────────────────────────


def test_normalize_accepts_compression_and_pct() -> None:
    raw = {
        "ts": 1_800_000_000,
        "agent_id": "coordinator",
        "agent_role": "coordinator",
        "model": "deepseek-chat",
        "usage": {"cache_hit_input": 10, "cache_miss_input": 90, "output": 20},
        "compression": {
            "count": 3,
            "saved_chars": 45_000,
            "by_method": {"fold_log": 2, "compact_json": 1},
        },
        "context_usage_pct": 62.5,
    }
    record = normalize_token_usage_record(raw)
    assert record is not None
    assert record["compression"]["count"] == 3
    assert record["compression"]["saved_chars"] == 45_000
    assert record["compression"]["by_method"] == {"fold_log": 2, "compact_json": 1}
    assert record["context_usage_pct"] == 62.5


def test_normalize_missing_new_fields_still_valid() -> None:
    """无压缩字段的旧记录仍能解析（向后兼容）。"""
    raw = {
        "ts": 1_800_000_000,
        "agent_id": "worker_1",
        "agent_role": "Worker",
        "model": "deepseek-chat",
        "usage": {"cache_hit_input": 0, "cache_miss_input": 50, "output": 10},
    }
    record = normalize_token_usage_record(raw)
    assert record is not None
    assert "compression" not in record
    assert "context_usage_pct" not in record


def test_normalize_rejects_invalid_pct() -> None:
    raw = {
        "ts": 1_800_000_000,
        "agent_id": "coordinator",
        "agent_role": "coordinator",
        "model": "deepseek-chat",
        "usage": {"cache_hit_input": 0, "cache_miss_input": 1, "output": 1},
        "context_usage_pct": 250,  # 越界 -> 丢弃
    }
    record = normalize_token_usage_record(raw)
    assert record is not None
    assert "context_usage_pct" not in record


# ── aggregate：totals 三数汇总 ──────────────────────────────────


def _record(ts: int, *, compression=None, pct=None) -> dict:
    raw = {
        "ts": ts,
        "agent_id": "coordinator",
        "agent_role": "coordinator",
        "model": "deepseek-chat",
        "usage": {"cache_hit_input": 10, "cache_miss_input": 90, "output": 20},
    }
    if compression is not None:
        raw["compression"] = compression
    if pct is not None:
        raw["context_usage_pct"] = pct
    return raw


def test_aggregate_totals_include_compression_metrics() -> None:
    records = [
        _record(
            1_800_000_000,
            compression={"count": 2, "saved_chars": 30_000, "by_method": {"fold_log": 2}},
            pct=40.0,
        ),
        _record(
            1_800_000_100,
            compression={"count": 1, "saved_chars": 15_000, "by_method": {"compact_json": 1}},
            pct=62.5,
        ),
        _record(1_800_000_200),  # 无压缩记录
    ]
    agg = aggregate_token_usage(records, current_model="deepseek-chat")
    totals = agg["totals"]
    assert totals["compression_count"] == 3
    assert totals["saved_chars"] == 45_000
    assert totals["compression_by_method"] == {"fold_log": 2, "compact_json": 1}
    # 占用率取范围内最新一条（ts 最大且有值）
    assert totals["context_usage_pct"] == 62.5


def test_aggregate_without_compression_records() -> None:
    records = [_record(1_800_000_000), _record(1_800_000_100)]
    agg = aggregate_token_usage(records, current_model="deepseek-chat")
    totals = agg["totals"]
    assert totals["compression_count"] == 0
    assert totals["saved_chars"] == 0
    assert totals["compression_by_method"] == {}
    assert totals["context_usage_pct"] is None


# ── [v1.0.34-M4-v2] 瞬时组 + 投影保留条数 ──────────────────────


def _record_v2(ts: int, *, compression=None, pct=None, projected=None) -> dict:
    raw = _record(ts, compression=compression, pct=pct)
    if projected is not None:
        raw["projected_message_count"] = projected
    return raw


def test_normalize_accepts_projected_message_count() -> None:
    raw = {
        "ts": 1_800_000_000,
        "agent_id": "coordinator",
        "agent_role": "coordinator",
        "model": "deepseek-chat",
        "usage": {"cache_hit_input": 10, "cache_miss_input": 90, "output": 20},
        "projected_message_count": 8,
    }
    record = normalize_token_usage_record(raw)
    assert record is not None
    assert record["projected_message_count"] == 8


def test_normalize_rejects_invalid_projected_count() -> None:
    raw = {
        "ts": 1_800_000_000,
        "agent_id": "coordinator",
        "agent_role": "coordinator",
        "model": "deepseek-chat",
        "usage": {"cache_hit_input": 0, "cache_miss_input": 1, "output": 1},
        "projected_message_count": -5,  # 非正 -> 丢弃
    }
    record = normalize_token_usage_record(raw)
    assert record is not None
    assert "projected_message_count" not in record


def test_aggregate_totals_include_latest_and_projected() -> None:
    records = [
        _record_v2(
            1_800_000_000,
            compression={"count": 2, "saved_chars": 30_000, "by_method": {"fold_log": 2}},
            pct=40.0,
            projected=6,
        ),
        _record_v2(1_800_000_100, projected=8),  # 无压缩，有投影
        _record_v2(1_800_000_200),  # 都无
    ]
    agg = aggregate_token_usage(records, current_model="deepseek-chat")
    totals = agg["totals"]
    # 累计
    assert totals["compression_count"] == 2
    assert totals["saved_chars"] == 30_000
    assert totals["projected_count"] == 14  # 6 + 8
    # 瞬时组：最新一条含压缩 = ts 最小那条（只有它有压缩）
    assert totals["latest_compression_count"] == 2
    assert totals["latest_saved_chars"] == 30_000
    # 瞬时投影：最新一条含投影 = ts 100 那条
    assert totals["latest_projected_count"] == 8
    # 占用率：最新一条含 pct
    assert totals["context_usage_pct"] == 40.0


def test_aggregate_latest_overwrites_by_ts_order() -> None:
    records = [
        _record_v2(
            1_800_000_000,
            compression={"count": 2, "saved_chars": 30_000, "by_method": {"fold_log": 2}},
            projected=6,
        ),
        _record_v2(
            1_800_000_100,
            compression={"count": 5, "saved_chars": 90_000, "by_method": {"compact_json": 1}},
            projected=9,
        ),
    ]
    agg = aggregate_token_usage(records, current_model="deepseek-chat")
    totals = agg["totals"]
    # 瞬时组取 ts 更大那条
    assert totals["latest_compression_count"] == 5
    assert totals["latest_saved_chars"] == 90_000
    assert totals["latest_projected_count"] == 9
    # 累计仍是总和
    assert totals["compression_count"] == 7
    assert totals["saved_chars"] == 120_000
    assert totals["projected_count"] == 15


def test_aggregate_latest_all_none_without_data() -> None:
    records = [_record_v2(1_800_000_000), _record_v2(1_800_000_100)]
    agg = aggregate_token_usage(records, current_model="deepseek-chat")
    totals = agg["totals"]
    assert totals["latest_compression_count"] is None
    assert totals["latest_saved_chars"] is None
    assert totals["latest_projected_count"] is None
    assert totals["projected_count"] == 0
