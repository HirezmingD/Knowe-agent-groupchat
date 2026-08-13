"""v1.0.17 — conformance tests for the Runtime repositioning invariants.

These are *fitness functions*: they fail if anyone re-introduces a judge into the
Runtime. Most are source-level (they read the module text and assert the presence or
absence of a pattern), so they run without the provider/browser dependencies and act
as durable guards against regression. One functional unit exercises the provider-
boundary well-formedness rule directly.

Invariants (see design/2-PRD.md):
  I-1 no result adjudication      I-4 no effort adjudication by proxy
  I-2 no intent inference         I-5 no framework-authored context
  I-3 no output rewriting         I-6 provider-boundary well-formedness
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]          # .../repo/backend package
PKG = BACKEND                                           # normalized single-layer package
CORE = BACKEND / "knowe_core"
HARNESS = BACKEND / "knowe_harness"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── I-1 · no result adjudication ────────────────────────────────────────────

def test_runtime_emits_no_work_quality_verdict() -> None:
    text = _src(PKG / "runtime.py")
    for banned in (
        "completion_requirements_unsatisfied",
        "max_turns_exceeded",
        "repeated_tool_failure",
        "artifact_reverification_failed",
        "_validate_completion",
        "_reverify_artifacts",
    ):
        assert banned not in text, f"[I-1] runtime.py still references judge symbol: {banned}"


# ── I-2 · no intent inference ───────────────────────────────────────────────

def test_gateway_does_not_parse_the_goal() -> None:
    text = _src(PKG / "worker_gateway_runtime.py")
    # Symbols may be *named* in an explanatory comment, but must not be defined or used.
    for sym in ("_PATH_TOKEN_RE", "_MUTATION_RE", "_DELETE_RE", "_goal_paths", "_structured_paths"):
        assert f"{sym} =" not in text, f"[I-2] gateway still defines {sym}"
        assert f"{sym}(" not in text, f"[I-2] gateway still calls {sym}"
    assert ".findall(" not in text, "[I-2] gateway still regex-scans text"
    assert "re.compile(" not in text, "[I-2] gateway still compiles a goal-parsing regex"


def test_worker_prompt_carries_no_inferred_expectations() -> None:
    text = _src(PKG / "runtime.py")
    # The initial task payload must not inject inferred expected artifacts/deletions.
    assert '"expected_artifact_paths": list(expected)' not in text, \
        "[I-2] initial worker prompt still injects inferred expected_artifact_paths"


# ── I-4 · no effort adjudication by proxy ───────────────────────────────────

def test_runtime_config_has_no_effort_knobs() -> None:
    text = _src(PKG / "runtime.py")
    for banned in ("max_turns", "max_same_error", "max_corrections"):
        assert banned not in text, f"[I-4] RuntimeConfig/loop still uses an effort knob: {banned}"


def test_run_loop_is_unbounded() -> None:
    text = _src(PKG / "runtime.py")
    assert "while True:" in text, "[I-4] run() is no longer an unbounded carrier loop"
    assert "for turn in range(" not in text, "[I-4] run() still caps turns with a range()"


# ── I-3 · no output rewriting ───────────────────────────────────────────────

def test_worker_text_is_passed_through_not_templated() -> None:
    engine = _src(PKG / "engine.py")
    assert "render_user_facing_completion(" not in engine, \
        "[I-3] engine still templates the Worker's final text"
    assert "rendered_text = raw_final or self._completion_message_text" in engine, \
        "[I-3] engine no longer passes the Worker's raw final text through"
    wc = _src(PKG / "worker_completion.py")
    assert "def render_user_facing_completion(" not in wc, \
        "[I-3] the templating renderer still exists"
    assert "本任务无文件产物" not in wc, "[I-3] placeholder sentence still present"


# ── I-5 · no framework-authored context ─────────────────────────────────────

def test_no_correction_injection() -> None:
    text = _src(PKG / "runtime.py")
    assert "_correction_message" not in text, "[I-5] runtime still injects framework corrections"


# ── completeness · single review channel ────────────────────────────────────

def test_single_coordinator_review_channel() -> None:
    proj = _src(HARNESS / "projections.py")
    # The harness free-text projection must no longer notify the coordinator.
    assert "notifier = getattr(self.engine, \"notify_coordinator\"" not in proj, \
        "[单通道] harness _coordinator still fires a second coordinator notice"
    engine = _src(PKG / "engine.py")
    assert 'notification_id = f"completion-review:{cid}:v{version}"' in engine, \
        "[单通道] notify_coordinator lacks the canonical (completion,version) identity"


# ── I-6 · provider-boundary well-formedness (functional unit) ───────────────

def _load_messages_module() -> types.ModuleType:
    """Load knowe_core.messages in isolation (its only dep is the light errors module),
    so the unit runs without provider/browser packages."""
    if "knowe_core" not in sys.modules:
        pkg = types.ModuleType("knowe_core")
        pkg.__path__ = [str(CORE)]
        sys.modules["knowe_core"] = pkg
    if "knowe_core.errors" not in sys.modules:
        espec = importlib.util.spec_from_file_location("knowe_core.errors", CORE / "errors.py")
        emod = importlib.util.module_from_spec(espec)
        assert espec and espec.loader
        espec.loader.exec_module(emod)
        sys.modules["knowe_core.errors"] = emod
    spec = importlib.util.spec_from_file_location("knowe_core.messages", CORE / "messages.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_sanitize_drops_malformed_empty_assistant_frame() -> None:
    messages = _load_messages_module()
    convo = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},          # malformed empty frame
        {"role": "assistant", "content": None},        # malformed empty frame
        {"role": "assistant", "content": "  \n "},     # whitespace-only
        {"role": "assistant", "content": "real answer"},
    ]
    out = messages.sanitize_messages(convo)
    assistants = [m for m in out if m.get("role") == "assistant"]
    assert len(assistants) == 1, "[I-6] empty assistant frames were not dropped"
    assert assistants[0]["content"] == "real answer"


def test_sanitize_keeps_toolcall_only_assistant_frame() -> None:
    messages = _load_messages_module()
    convo = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "{}"},
    ]
    out = messages.sanitize_messages(convo)
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in out), \
        "[I-6] a valid tool_call-only assistant frame was wrongly dropped"


def test_actor_scope_breaks_inherited_visible_activity_emitter() -> None:
    from backend import tool_ledger

    parent_events: list[str] = []

    async def parent_emitter(text: str) -> None:
        parent_events.append(text)

    async def scenario() -> None:
        wrapped = tool_ledger.instrument("safe_read_file", lambda _args: {"status": "ok"})
        with tool_ledger.actor_scope(activity_emitter=parent_emitter) as parent:
            async def worker_child() -> tool_ledger.ToolAudit:
                # This is the Actor boundary used by WorkerRuntimeFactory.execution_context.
                with tool_ledger.actor_scope() as child:
                    await wrapped({"path": "report.md"})
                    return child

            child = await asyncio.create_task(worker_child())
            assert tool_ledger.current() is parent
            assert parent.actions == []
            assert child.actions and child.actions[0]["name"] == "safe_read_file"
        assert tool_ledger.current() is None

    asyncio.run(scenario())
    assert parent_events == [], "Worker tool activity leaked through the Coordinator emitter"


# ── standalone runner (offline: pytest may be unavailable) ──────────────────

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"FAIL  {fn.__name__}: {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(fns)} total")
    sys.exit(1 if failed else 0)
