"""v1.0.18 Coordinator AgentLoop freedom/integrity regressions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowe_core.agent_loop import AgentLoop, AgentLoopConfig
from knowe_core.tool_registry import ToolRegistry

from tests._fakes import FakeProvider, text_turn, tool_turn


def _registry_with_read():
    reg = ToolRegistry()
    calls: list[dict] = []

    def _read(args, **_ctx):
        calls.append(args)
        return '{"status":"ok","content":"<div class=\\"chapter-body\\">...</div>"}'

    for name in ("safe_read_file", "safe_search_files", "safe_list_dir"):
        reg.register(
            name=name,
            description="read",
            parameters={"type": "object"},
            handler=_read,
        )
    return reg, calls


def _cfg() -> AgentLoopConfig:
    return AgentLoopConfig(
        system_prompt="sys",
        messages=[{"role": "user", "content": "看一下第二章"}],
    )


@pytest.mark.asyncio
async def test_action_sounding_plain_text_is_authoritative_final() -> None:
    """Runtime must not decide that ordinary prose sounds unfinished."""

    provider = FakeProvider([
        text_turn("好的，让我看看 index.html 第 631 行附近的 chapter-body。"),
        tool_turn("safe_read_file", {"path": "index.html"}),
    ])
    reg, read_calls = _registry_with_read()

    result = await AgentLoop(client=provider, registry=reg).run(_cfg())

    assert result.final_response == "好的，让我看看 index.html 第 631 行附近的 chapter-body。"
    assert read_calls == []
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_tool_choice_is_never_escalated_to_required() -> None:
    provider = FakeProvider([
        tool_turn("safe_read_file", {"path": "index.html"}),
        text_turn("读完了。"),
    ])
    reg, read_calls = _registry_with_read()

    result = await AgentLoop(client=provider, registry=reg).run(_cfg())

    assert result.final_response == "读完了。"
    assert read_calls == [{"path": "index.html"}]
    assert provider.call_count == 2
    assert provider.tool_choice_of(0) == "auto"
    assert provider.tool_choice_of(1) == "auto"


@pytest.mark.asyncio
async def test_clean_chat_ends_without_hidden_retry() -> None:
    provider = FakeProvider([text_turn("你好！你想写什么题材的小说？")])
    reg, _ = _registry_with_read()

    result = await AgentLoop(client=provider, registry=reg).run(_cfg())

    assert result.final_response.startswith("你好")
    assert result.iterations == 1
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_repeated_action_phrase_is_not_semantically_retried() -> None:
    provider = FakeProvider([
        text_turn("让我看看 index.html 第二章。"),
        text_turn("让我再看看 index.html 第二章。"),
    ])
    reg, read_calls = _registry_with_read()

    result = await AgentLoop(client=provider, registry=reg).run(_cfg())

    assert result.final_response == "让我看看 index.html 第二章。"
    assert read_calls == []
    assert provider.call_count == 1
