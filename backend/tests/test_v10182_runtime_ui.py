"""v1.0.18.2 — Runtime 可见阶段、帧保护配套契约与称呼链路回归。"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from backend import contract, runtime_settings, tool_ledger
from backend.engine import ProjectEngine, _engine_block
from backend.runtime import WORKER_TOOL_NAMES
from backend.worker_gateway_runtime import _inject_user_address_prompt


ACTION_CONTRACT = _engine_block("ACTION_CONTRACT", lang="zh")


EXPECTED_FIXED_19 = (
    "safe_read_file",
    "safe_write_file",
    "safe_patch",
    "safe_list_dir",
    "safe_search_files",
    "safe_delete_file",
    "read_external_file",
    "list_external_dir",
    "copy_external_file",
    "safe_bash",
    "web_search",
    "web_extract",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_scroll",
    "browser_back",
    "browser_screenshot",
)

EXPECTED_STAGE_BY_TOOL = {
    "safe_read_file": "integrate",
    "safe_write_file": "implement",
    "safe_patch": "implement",
    "safe_list_dir": "explore",
    "safe_search_files": "explore",
    "safe_delete_file": "implement",
    "read_external_file": "integrate",
    "list_external_dir": "explore",
    "copy_external_file": "implement",
    "safe_bash": "verify",
    "web_search": "explore",
    "web_extract": "integrate",
    "browser_navigate": "explore",
    "browser_snapshot": "integrate",
    "browser_click": "implement",
    "browser_type": "implement",
    "browser_scroll": "integrate",
    "browser_back": "integrate",
    "browser_screenshot": "verify",
}


def test_fixed_19_names_and_order_remain_byte_for_byte_canonical() -> None:
    assert WORKER_TOOL_NAMES == EXPECTED_FIXED_19


def test_every_fixed_worker_tool_maps_to_one_observable_public_stage() -> None:
    assert {
        name: tool_ledger.stage_for_tool(name)
        for name in WORKER_TOOL_NAMES
    } == EXPECTED_STAGE_BY_TOOL

    payload = tool_ledger.stage_payload("safe_read_file")
    assert payload == {
        "stage": "integrate",
        "stage_detail": "正在整合信息并理解现有内容",
        "stage_state": "active",
    }


def test_tool_activity_detail_reuses_existing_tool_name_field() -> None:
    token = tool_ledger.activity_token("safe_read_file", {"path": "src/store/state.ts"})
    assert token == f"safe_read_file{tool_ledger.TOOL_ACTIVITY_SEPARATOR}src/store/state.ts"
    assert tool_ledger.base_tool_name(token) == "safe_read_file"


def test_existing_visible_events_accept_optional_eight_stage_projection() -> None:
    base = {"project_id": "p1", "seq": 1, "ts": "2026-07-30T00:00:00Z"}
    contract.validate_outbound({
        **base,
        "type": "agent_thinking",
        "agent_id": "worker-a",
        "phase": "thinking",
        "stage": "plan",
        "stage_detail": "正在规划下一步处理方式",
        "stage_state": "active",
    })
    contract.validate_outbound({
        **base,
        "type": "tool_gen",
        "agent_id": "worker-a",
        "tool_name": "safe_read_file",
        **tool_ledger.stage_payload("safe_read_file"),
    })
    contract.validate_outbound({
        **base,
        "type": "message",
        "agent_id": "worker-a",
        "content": "完成",
        "stage": "deliver",
        "stage_state": "complete",
    })
    contract.validate_outbound({
        **base,
        "type": "error",
        "agent_id": "worker-a",
        "message": "失败",
        "stage": "verify",
        "stage_state": "error",
    })


def test_worker_prompt_appends_current_user_address_at_high_attention_edge() -> None:
    runtime = SimpleNamespace(prompt="WORKER BASE PROMPT\n")
    returned = _inject_user_address_prompt(runtime, {
        "user_address": "【用户称呼（当前唯一有效）】屏幕前用户当前称呼是「小满」。",
    })

    assert returned is runtime
    assert runtime.prompt == (
        "WORKER BASE PROMPT\n\n"
        "【用户称呼（当前唯一有效）】屏幕前用户当前称呼是「小满」。"
    )

    empty = SimpleNamespace(prompt="UNCHANGED")
    _inject_user_address_prompt(empty, {})
    assert empty.prompt == "UNCHANGED"


def test_coordinator_rename_counter_is_strong_and_one_shot(monkeypatch) -> None:
    current = {"value": "新称呼"}
    monkeypatch.setattr(
        runtime_settings,
        "user_name",
        lambda default="": current["value"] or default,
    )
    engine = object.__new__(ProjectEngine)
    engine._last_user_address_name = "旧称呼"

    first = engine._coordinator_user_address_block()
    assert "当前唯一有效" in first
    assert "必须使用「新称呼」" in first
    assert "从「旧称呼」改为「新称呼」" in first
    assert "旧称立即作废" in first

    second = engine._coordinator_user_address_block()
    assert "当前唯一有效" in second
    assert "称呼刚刚更新" not in second
    assert "旧称呼" not in second

    current["value"] = ""
    cleared = engine._coordinator_user_address_block()
    assert "已清除称呼「新称呼」" in cleared
    assert "不得继续沿用历史旧称" in cleared


def test_coordinator_address_block_sits_immediately_before_action_contract() -> None:
    source = inspect.getsource(ProjectEngine._run_agent_turn)
    address = '+ (("\\n\\n" + user_address_block) if user_address_block else "")'
    action = '+ "\\n\\n" + _engine_block("ACTION_CONTRACT")'
    assert address in source
    assert action in source
    assert source.index(address) < source.index(action)
    assert ACTION_CONTRACT.startswith("═")


def test_worker_envelope_refreshes_address_immediately_before_runtime_creation() -> None:
    source = inspect.getsource(ProjectEngine._run_worker_attempt)
    refresh = '"user_address": self._user_address_line()'
    create = 'create = getattr(self._worker_runtime_factory, "create", None)'
    assert refresh in source
    assert create in source
    assert source.index(refresh) < source.index(create)
