"""v1.0.17.x — Coordinator prose is not a Worker-status protocol.

The old v0.22-v0.28 tests intentionally grew an open-set natural-language judge.
The v1.0.17.x architecture removes that responsibility: roster/completion
facts are structured inputs, while ordinary final prose passes through unchanged.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import backend.engine as engine_module
from backend.engine import ACTION_CONTRACT, COORDINATOR, COORDINATOR_ROLE, ProjectEngine
from backend.hub import Hub


class _FakeCoordinator:
    def __init__(self, final: str) -> None:
        self.agent_id = COORDINATOR
        self.role = COORDINATOR_ROLE
        self.ephemeral_system_prompt = ""
        self._history: list[dict] = []
        self.stream_delta_callback = None
        self.tool_gen_callback = None
        self.tool_start_callback = None
        self.tool_complete_callback = None
        self.final = final

    async def run_conversation(self, content: str, conversation_history=None, attachments=None):
        del content, conversation_history, attachments
        return {
            "final_response": self.final,
            "tool_calls": [],
            "iterations": 1,
            "is_interrupted": False,
            "error": None,
            "new_messages": [],
        }

    def interrupt(self) -> None:  # pragma: no cover - AgentPort compatibility
        return None

    def steer(self, text: str) -> None:  # pragma: no cover
        del text

    def request_wrapup(self, reason: str = "") -> None:  # pragma: no cover
        del reason

    @property
    def history(self):
        return self._history


def _engine_with_final(final: str, *, busy: bool) -> tuple[ProjectEngine, list[dict]]:
    root = Path(tempfile.mkdtemp())
    engine = ProjectEngine(Hub(), "p1", agent=None, workspace_root=root)
    engine._roster = {"writer_1": "技术写作"}
    engine._names["writer_1"] = "Shiloh"
    if busy:
        engine._workers_with_open_activity.add("writer_1")
    engine._agents[COORDINATOR] = _FakeCoordinator(final)
    emitted: list[dict] = []
    original_emit = engine.emit

    async def capture(event: dict, *, channel: str | None = None):
        emitted.append(dict(event))
        return await original_emit(event, channel=channel)

    engine.emit = capture  # type: ignore[method-assign]
    return engine, emitted


ORDINARY_PROSE = (
    "Shiloh 已经去打开主页截图了，等他交报告我来审阅。",
    "去了，Shiloh 正在执行这个任务。",
    "Shiloh 的报告我正在看，写得不错。",
    "你要不要让 Shiloh 去做这件事？",
    "如果 Shiloh 有空，就请他先看看。",
    "并不是 Shiloh 在处理，这只是引用：‘他正在做’。",
    "等他交报告我再告诉你。",
    "What if Shiloh is already working on it?",
    "Shiloh is not working; I am only quoting ‘he is working’.",
    "When they finish, I can review the report.",
    "他说‘Wolf 正在做’，但这句话未必是真的。",
    "队里有 Shiloh（技术写作），这不代表他此刻在忙。",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", [False, True], ids=["idle-roster", "busy-roster"])
@pytest.mark.parametrize("final", ORDINARY_PROSE)
async def test_ordinary_prose_passes_without_open_set_adjudication(final: str, busy: bool) -> None:
    """Names, pronouns, tense, quotation, negation and conditions are not protocols."""

    engine, emitted = _engine_with_final(final, busy=busy)
    await engine._run_agent_turn("请正常回答")
    messages = [event for event in emitted if event.get("type") == "message"]
    assert messages
    assert messages[-1].get("content") == final
    assert not [event for event in emitted if event.get("type") == "error"]


def test_open_set_worker_status_judge_is_physically_removed() -> None:
    source = Path(engine_module.__file__).read_text("utf-8")
    for removed in (
        "_phantom_work_claim",
        "_off_roster_worker_claim",
        "_coordinator_misstatement",
        "_strip_phantom_sentences",
        "_ASSIGN_DONE_PHRASES",
        "_PROPOSE_CLAIM_PHRASES",
        "_IMMUTABLE_PHRASES",
    ):
        assert removed not in source
        assert not hasattr(ProjectEngine, removed)


def test_authoritative_status_is_prompt_input_not_outbound_text_parser() -> None:
    engine, _ = _engine_with_final("正常回答", busy=False)
    idle = engine._work_status_ctx()
    engine._workers_with_open_activity.add("writer_1")
    busy = engine._work_status_ctx()
    assert "没有任何成员在执行任务" in idle
    assert "Shiloh" in busy and "正在执行任务" in busy
    assert "roster" in ACTION_CONTRACT
    assert "completion" in ACTION_CONTRACT
    assert "普通自然语言不是状态协议" in ACTION_CONTRACT


@pytest.mark.asyncio
async def test_committed_dispatch_never_rewrites_authoritative_final_prose() -> None:
    """A structured dispatch card does not license sentence-level prose deletion."""

    source = Path(engine_module.__file__).read_text("utf-8")
    body = source.split("async def _run_agent_turn(", 1)[1].split("\n    async def ", 1)[0]
    assert "_strip_dispatch_echo" not in source
    assert "if final and self._dispatched_this_turn:" not in body
    assert not hasattr(ProjectEngine, "_strip_dispatch_echo")

    final = "林知远去写登录页。我还需要你确认注册页范围。"
    engine, emitted = _engine_with_final(final, busy=False)
    engine._roster.update({"fe_1": "前端"})
    engine._names["fe_1"] = "林知远"
    engine._dispatched_this_turn.append("fe_1")
    await engine._run_agent_turn("继续")
    messages = [event for event in emitted if event.get("type") == "message"]
    assert messages[-1].get("content") == final


def test_action_contract_remains_last_and_short() -> None:
    source = Path(engine_module.__file__).read_text("utf-8")
    block = source.split("agent = self._get_or_create_coordinator()", 1)[1].split(
        "self.repair_agent_history(agent)", 1
    )[0]
    assert block.rindex("ACTION_CONTRACT") > block.rindex("dm_context")
    assert len(ACTION_CONTRACT) < 1000
