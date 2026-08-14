"""v1.0.34 M1 幂等契约 + content_compress 行为用例。

契约四条：
1. 确定性：同输入两次 compress，字节级一致
2. 幂等：compress(compress(x)) == compress(x)
3. fail-closed：非法输入（非 UTF-8 序列、异常形状）透传原文
4. 诚实零：短内容、不可压内容 method="passthrough"，返回原文

行为用例：detect 三分类 / fold_repeated_lines / compact_json（含 round-trip）。
"""

from __future__ import annotations

import json

import pytest

from backend import content_compress as ccom


def _bytes_identical(a: str, b: str) -> bool:
    return a.encode("utf-8") == b.encode("utf-8")


# ── 契约 1：确定性 ──────────────────────────────────────────────


def test_compress_is_deterministic() -> None:
    """同输入两次 compress，字节级一致。"""
    text = "[INFO] a\n[INFO] b\n" * 50
    first, meta_first = ccom.compress(text)
    second, meta_second = ccom.compress(text)
    assert _bytes_identical(first, second)
    assert meta_first == meta_second


# ── 契约 2：幂等 ────────────────────────────────────────────────


def test_compress_is_idempotent() -> None:
    """compress(compress(x)) == compress(x)，字节级一致。"""
    text = "[INFO] a\n[INFO] b\n" * 50
    once, _ = ccom.compress(text)
    twice, _ = ccom.compress(once)
    assert _bytes_identical(twice, once)


def test_fold_is_idempotent() -> None:
    """fold 产物再 fold 不变（标记行不是重复行）。"""
    text = ("ERROR connection timeout\n" * 40) + "INFO done\n"
    once, meta = ccom.fold_repeated_lines(text)
    assert meta["method"] == "fold_log"
    twice, meta2 = ccom.fold_repeated_lines(once)
    assert twice == once
    assert meta2["method"] == "passthrough"


def test_compact_is_idempotent() -> None:
    """compact 产物再 compact 不变（重编码格式不被误判为 JSON）。"""
    text = json.dumps([{"name": "a", "qty": 1}, {"name": "b", "qty": 2}])
    once, meta = ccom.compact_json(text)
    assert meta["method"] == "compact_json"
    twice, meta2 = ccom.compact_json(once)
    assert twice == once
    assert meta2["method"] == "passthrough"


# ── 契约 3：fail-closed ─────────────────────────────────────────


def test_invalid_utf8_passthrough() -> None:
    """非 UTF-8 序列透传原文，method="passthrough"。"""
    raw = b"abc\xff\xfe\x00def"
    text = raw.decode("utf-8", errors="replace")
    out, meta = ccom.compress(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_unusual_shape_passthrough() -> None:
    """异常形状输入（空串、纯空白、单行超长无换行）透传原文。"""
    for text in ("", "   ", "x" * 3000, "no newline here " * 200):
        out, meta = ccom.compress(text)
        assert out == text
        assert meta["method"] == "passthrough"


# ── 契约 4：诚实零 ──────────────────────────────────────────────


def test_short_content_passthrough() -> None:
    """短内容（低于压缩门槛）method="passthrough"，返回原文。"""
    text = "hello world"
    out, meta = ccom.compress(text)
    assert out == text
    assert meta["method"] == "passthrough"
    assert meta["before_chars"] == len(text)
    assert meta["after_chars"] == len(text)


def test_incompressible_content_passthrough() -> None:
    """不可压内容（无重复行、无 JSON 结构）method="passthrough"。"""
    text = "\n".join(f"line {i} is unique content here" for i in range(500))
    out, meta = ccom.compress(text)
    assert out == text
    assert meta["method"] == "passthrough"


# ── detect：三分类 ──────────────────────────────────────────────


def test_detect_json_object_and_array() -> None:
    assert ccom.detect('{"status": "ok", "n": 3}') == "json"
    assert ccom.detect('[1, 2, 3]') == "json"
    assert ccom.detect('  {"a": 1}') == "json"  # 前导空白容忍


def test_detect_log_levels() -> None:
    log = "[INFO] start\n[ERROR] boom\n[WARN] careful\n[DEBUG] trace\n"
    assert ccom.detect(log) == "log"


def test_detect_log_timestamps() -> None:
    log = "2026-08-13 10:00:01 started\n2026-08-13 10:00:02 running\n12:30:45 done\n"
    assert ccom.detect(log) == "log"


def test_detect_plain_fallback() -> None:
    assert ccom.detect("hello world\nsecond line\n") == "plain"
    assert ccom.detect("line one\nline two\nline three\nline four\n") == "plain"


def test_detect_log_with_code_keywords() -> None:
    """日志里带代码关键词仍判 log（级别词命中过半优先于代码内容）。"""
    log = (
        "INFO def main():\n"
        "INFO     print('hello')\n"
        "ERROR TypeError: unsupported operand\n"
        "INFO     return 42\n"
    )
    assert ccom.detect(log) == "log"


def test_detect_broken_json_falls_to_plain() -> None:
    """'{' 开头但解析失败 -> plain（fail-open 到最保守路径）。"""
    assert ccom.detect("{not valid json at all") == "plain"


# ── fold_repeated_lines ─────────────────────────────────────────


def test_fold_40_identical_lines_to_one_marker() -> None:
    text = ("ERROR connection timeout\n" * 40) + "INFO done\n"
    out, meta = ccom.fold_repeated_lines(text)
    assert meta["method"] == "fold_log"
    assert out == "… 40 lines elided (knowe) …\nINFO done\n"


def test_fold_run_below_threshold_untouched() -> None:
    text = "ERROR a\nERROR a\nINFO ok\n"
    out, meta = ccom.fold_repeated_lines(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_fold_all_unique_lines_untouched() -> None:
    text = "\n".join(f"unique line {i}" for i in range(100))
    out, meta = ccom.fold_repeated_lines(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_fold_short_run_marker_longer_than_original_kept() -> None:
    """折叠产物 >= 原 run 总长 -> 保留原行（诚实零）。"""
    text = "a\n" * 3  # 3 行各 1 字符 + 换行；标记行更长
    out, meta = ccom.fold_repeated_lines(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_fold_blank_lines_run() -> None:
    """空行 run>=3 折叠（40 个空行：标记行 29 字符 < 原文 40 字符，折叠划算）。"""
    text = "INFO a\n" + "\n" * 40 + "INFO b\n"
    out, meta = ccom.fold_repeated_lines(text)
    assert meta["method"] == "fold_log"
    assert "… 40 lines elided (knowe) …" in out
    assert out.startswith("INFO a\n")
    assert out.endswith("INFO b\n")


def test_fold_blank_lines_short_run_honest_zero() -> None:
    """空行 run 短、标记行比原文长 -> 保留原文（诚实零）。"""
    text = "INFO a\n\n\n\n\nINFO b\n"  # 4 个空行
    out, meta = ccom.fold_repeated_lines(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_fold_mixed_runs_and_context_kept() -> None:
    text = "INFO start\nERROR boom\n" + ("INFO retry\n" * 10) + "INFO done\n"
    out, meta = ccom.fold_repeated_lines(text)
    assert meta["method"] == "fold_log"
    assert out == "INFO start\nERROR boom\n… 10 lines elided (knowe) …\nINFO done\n"


def test_fold_crlf_compatible() -> None:
    text = "ERROR boom\r\n" * 5 + "INFO ok\r\n"
    out, meta = ccom.fold_repeated_lines(text)
    assert meta["method"] == "fold_log"
    assert "… 5 lines elided (knowe) …" in out
    assert out.endswith("INFO ok\r\n")


def test_fold_lines_with_line_number_prefix() -> None:
    """safe_read_file 注入行号前缀（NNN│ ）后仍折叠：剥离行号判定重复 run。"""
    text = "".join(f"{i:>4}│ ERROR connection timeout\n" for i in range(1, 41)) + "INFO done\n"
    out, meta = ccom.fold_repeated_lines(text)
    assert meta["method"] == "fold_log"
    assert out == "… 40 lines elided (knowe) …\nINFO done\n"


def test_fold_line_number_prefix_different_content_kept() -> None:
    """剥离行号后内容仍不同 -> 不折叠（防误伤）。"""
    text = "".join(f"{i:>4}│ ERROR code {i}\n" for i in range(1, 41))
    out, meta = ccom.fold_repeated_lines(text)
    assert out == text
    assert meta["method"] == "passthrough"


# ── compact_json ────────────────────────────────────────────────


def test_compact_homogeneous_object_array_roundtrip() -> None:
    original = [{"name": "a", "qty": 1}, {"name": "b", "qty": 2}]
    text = json.dumps(original, ensure_ascii=False)
    out, meta = ccom.compact_json(text)
    assert meta["method"] == "compact_json"
    assert out.startswith("rows[2]{name,qty}:")
    assert ccom.decode(out) == original


def test_compact_scalar_array_roundtrip() -> None:
    original = list(range(200))  # 大数组：json.dumps 的 ", " 分隔让重编码明显更短
    text = json.dumps(original)
    out, meta = ccom.compact_json(text)
    assert meta["method"] == "compact_json"
    assert out.startswith("values[200]:")
    assert ccom.decode(out) == original


def test_compact_small_scalar_array_honest_zero() -> None:
    """小标量数组：重编码不比原文短 -> 透传（诚实零）。"""
    original = [1, 2, 3]
    text = json.dumps(original)
    out, meta = ccom.compact_json(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_compact_strings_with_quotes_roundtrip() -> None:
    original = [{"k": "a,b:c"}, {"k": 'say "hi"'}]
    text = json.dumps(original, ensure_ascii=False)
    out, meta = ccom.compact_json(text)
    assert meta["method"] == "compact_json"
    assert ccom.decode(out) == original


def test_compact_nested_object_passthrough() -> None:
    text = json.dumps([{"a": {"b": 1}}])
    out, meta = ccom.compact_json(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_compact_non_object_shapes_passthrough() -> None:
    for payload in ('{"a": 1}', '["x", "y"]', "[1, [2]]", "[]", "null"):
        text = payload if payload != '{"a": 1}' else '{"a": 1}'
        out, meta = ccom.compact_json(text)
        assert out == text
        assert meta["method"] == "passthrough"


def test_compact_not_shorter_passthrough() -> None:
    """产物不短于原文 -> 透传（诚实零）。"""
    text = json.dumps([{"x": 1}])  # 值极短，重编码必然更长
    out, meta = ccom.compact_json(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_compress_routes_json_to_compact() -> None:
    # 200 个对象 ≈ 4.6K 字符，超过 2000 门槛，触发压缩
    text = json.dumps([{"name": "a", "qty": 1}] * 200)
    out, meta = ccom.compress(text)
    assert meta["method"] == "compact_json"
    assert out.startswith("rows[200]")


def test_compress_routes_log_to_fold() -> None:
    # 300 行 ≈ 4.2K 字符，超过 2000 门槛，触发压缩
    text = "INFO retrying\n" * 300 + "INFO done\n"
    out, meta = ccom.compress(text)
    assert meta["method"] == "fold_log"
    assert "… 300 lines elided (knowe) …" in out


# ── [v1.0.34-实测v2] 工具结果 JSON 包装解包 ────────────────────
# 实锤：工具结果全部是 {"status":"ok", ...} 包装（tools_knowe._ok），
# detect 判 json 后 compact_json 只认数组 → 对象透传 → fold_log 闲置。
# 新增 dict 分支：大字符串字段内部压缩（log→折叠 / json数组→rows），重组更短才生效。


def test_compact_wrapped_log_object() -> None:
    """{"status":"ok","output":"<60行相同ERROR>"} → 内部折叠为标记行。"""
    payload = "ERROR connection timeout retry pending\n" * 60 + "INFO done\n"
    text = json.dumps({"status": "ok", "output": payload, "exit_code": 1}, ensure_ascii=False)
    out, meta = ccom.compress(text)
    assert meta["method"] == "compact_json"
    assert "… 60 lines elided (knowe) …" in out
    assert out.count("ERROR connection timeout") == 0  # 折叠后重复行消失
    assert len(out) < len(text)


def test_compact_wrapped_log_object_idempotent() -> None:
    """包装解包产物再压缩不变（幂等）。"""
    payload = "ERROR retry\n" * 40
    text = json.dumps({"status": "ok", "output": payload}, ensure_ascii=False)
    once, _ = ccom.compress(text)
    twice, meta2 = ccom.compress(once)
    assert twice == once
    assert meta2["method"] == "passthrough"  # 二次无收益


def test_compact_wrapped_json_array() -> None:
    """{"status":"ok","data":[{同构数组}]} → 内部 rows 表。"""
    inner = json.dumps([{"name": f"n{i}", "qty": i} for i in range(100)], ensure_ascii=False)
    text = json.dumps({"status": "ok", "data": inner}, ensure_ascii=False)
    out, meta = ccom.compress(text)
    assert meta["method"] == "compact_json"
    assert "rows[100]{name,qty}" in out
    assert len(out) < len(text)


def test_compact_wrapped_object_small_field_passthrough() -> None:
    """包装内无大字段（<2000）→ 透传，诚实零。"""
    text = json.dumps({"status": "ok", "output": "tiny", "exit_code": 0}, ensure_ascii=False)
    out, meta = ccom.compress(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_compact_wrapped_object_no_benefit_passthrough() -> None:
    """包装内压缩后重组不比原文短 → 透传（诚实零）。"""
    payload = "ERROR x\n" * 4  # run=4 ≥3 折叠但收益小
    text = json.dumps({"status": "ok", "output": payload}, ensure_ascii=False)
    out, meta = ccom.compress(text)
    assert out == text
    assert meta["method"] == "passthrough"


def test_compress_wrapped_uses_min_chars_gate() -> None:
    """包装字段本身 <2000 门槛 → 不透传（门槛检查在字段级）。"""
    payload = "ERROR retry\n" * 25  # ~350 字符
    text = json.dumps({"status": "ok", "output": payload}, ensure_ascii=False)
    out, meta = ccom.compress(text)
    assert out == text
    assert meta["method"] == "passthrough"


# ── [v1.0.34-实测v3] Worker 真实形状：ToolResult.to_dict() 的 facts 嵌套 ──
# 实锤：runtime.py L1093 把工具结果包成 {call_id, name, ok, summary,
#   facts:{status, output:<大内容>}, ...} 才进 compress_tool_result——
#   dict 解包分支只遍历顶层 str 字段，facts 是 dict 被跳过 → 永远透传。
# 修法：解包分支对嵌套 dict/list 内的 str 字段同样尝试内部压缩（递归）。


def test_compact_worker_toolresult_nested_facts() -> None:
    """Worker 真实形状：大内容在 facts.output 嵌套 dict 里 → 必须压缩。"""
    payload = "ERROR connection timeout retry pending\n" * 100 + "INFO done\n"
    text = json.dumps(
        {
            "call_id": "call_abc",
            "name": "read_file",
            "ok": True,
            "summary": "read_file completed.",
            "facts": {"status": "ok", "path": "pytest_before.txt", "content": payload},
            "error": "",
            "error_code": "",
            "effect": "read",
            "duration_ms": 123,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    out, meta = ccom.compress(text)
    assert meta["method"] == "compact_json"
    assert "… 100 lines elided (knowe) …" in out
    assert out.count("ERROR connection timeout") == 0  # 嵌套大字段被折叠
    assert len(out) < len(text)


def test_compact_worker_toolresult_nested_facts_idempotent() -> None:
    """facts 嵌套压缩产物再压缩不变（幂等）。"""
    payload = "ERROR retry pending\n" * 300  # 300 行 × ~20 字符 > 2000 门槛
    text = json.dumps(
        {
            "call_id": "call_x",
            "name": "terminal",
            "ok": True,
            "summary": "terminal completed.",
            "facts": {"status": "ok", "output": payload, "exit_code": 1},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    once, _ = ccom.compress(text)
    twice, meta2 = ccom.compress(once)
    assert twice == once
    assert meta2["method"] == "passthrough"  # 二次无收益
    assert "… 300 lines elided (knowe) …" in once


def test_compact_worker_toolresult_nested_list_of_dicts() -> None:
    """facts.data 是嵌套数组（文件列表等）→ 数组内大字段同样处理。"""
    rows = [
        {"name": f"file_{i}.log", "size": 1024 * i, "lines": "ERROR x\n" * 100}
        for i in range(3)
    ]  # 每个 lines 字段 ~900 字符 × 3，单个仍 < 2000 → 数组整体不压（诚实零）
    text = json.dumps(
        {
            "call_id": "call_y",
            "name": "list_files",
            "ok": True,
            "summary": "listed.",
            "facts": {"status": "ok", "data": rows},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    out, meta = ccom.compress(text)
    assert out == text
    assert meta["method"] == "passthrough"  # 单字段均未超门槛 → 诚实零透传


def test_compact_worker_toolresult_nested_list_big_field() -> None:
    """facts.data 数组内单个大字段（>门槛）→ 递归压缩生效。"""
    rows = [
        {"name": "a.log", "size": 10},
        {"name": "b.log", "size": 20, "content": "ERROR repeated line\n" * 300},  # >2000
        {"name": "c.log", "size": 30},
    ]
    text = json.dumps(
        {
            "call_id": "call_z",
            "name": "list_files",
            "ok": True,
            "summary": "listed.",
            "facts": {"status": "ok", "data": rows},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    out, meta = ccom.compress(text)
    assert meta["method"] == "compact_json"
    assert "… 300 lines elided (knowe) …" in out
    assert len(out) < len(text)

