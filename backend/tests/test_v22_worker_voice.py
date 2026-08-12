"""v2.2 single Worker prompt and fixed tool-boundary tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend import tools_knowe
from backend.runtime import WORKER_TOOL_NAMES

PROMPT = Path(tools_knowe.__file__).with_name("worker_prompt.md")
SOUL = Path(tools_knowe.__file__).parent / "souls" / "worker.txt"


class FakeEngine:
    project_id = "project_20260723002000"
    workspace_root = Path("/tmp/ws")
    internal_workspace = Path("/tmp/int")


def worker_registry_names() -> tuple[str, ...]:
    return tuple(tools_knowe.build_worker_registry(FakeEngine(), "be_1").names())


def test_registry_is_the_exact_fixed_19_without_completion_pseudotools() -> None:
    names = worker_registry_names()
    assert names == WORKER_TOOL_NAMES
    assert len(names) == 19
    assert "submit_report" not in names
    assert "speak" not in names
    assert "read_result_ref" not in names


def test_only_one_canonical_worker_prompt_path_exists() -> None:
    assert PROMPT.is_file()
    backend_root = Path(tools_knowe.__file__).resolve().parents[1]
    assert not (backend_root / "knowe_prompts").exists()
    assert not (Path(tools_knowe.__file__).parent / "knowe_prompts").exists()


def test_prompt_requires_native_provider_tool_calls() -> None:
    prompt = PROMPT.read_text("utf-8")
    assert "Provider's native tool-call interface" in prompt
    assert "<tool_call>" in prompt
    assert "Never print, imitate, or wrap" in prompt


def test_prompt_describes_fixed_visibility_and_stable_unavailability() -> None:
    prompt = PROMPT.read_text("utf-8")
    assert "same 19 tools remain available on every turn" in prompt
    assert "service can be unavailable" in prompt
    assert "Do not invent another tool" in prompt


def test_prompt_uses_same_tool_continuation_not_result_refs() -> None:
    prompt = PROMPT.read_text("utf-8")
    assert "continue with the same tool" in prompt
    assert "offset" in prompt and "limit" in prompt
    assert "no secondary result-reference protocol" in prompt


def test_prompt_requires_verified_file_and_delete_effects() -> None:
    prompt = PROMPT.read_text("utf-8")
    assert "verified size and SHA-256 digest" in prompt
    assert "verified absent" in prompt
    assert "do not claim an effect" in prompt


def test_prompt_keeps_final_response_user_facing() -> None:
    prompt = PROMPT.read_text("utf-8")
    assert "concise, factual result" in prompt
    assert "project-relative deliverable paths" in prompt
    assert "Do not paste full tool results" in prompt


@pytest.mark.parametrize(
    "retired",
    [
        "submit_report",
        "调用 speak",
        "ContextBundleV2",
        "capability lease",
        "surface plan",
        "result store",
        "read_result_ref",
    ],
)
def test_retired_control_language_stays_out_of_active_prompt(retired: str) -> None:
    assert retired not in PROMPT.read_text("utf-8")


def test_retained_soul_points_to_canonical_prompt_and_does_not_define_another_protocol() -> None:
    soul = SOUL.read_text("utf-8")
    assert "worker_prompt.md" in soul
    assert "must not define a second protocol" in soul
    assert "Provider-native tool calls" in soul


@pytest.mark.parametrize(
    ("task_context", "expected_rule"),
    [
        ("请用中文完成分析", "explicit output-language requirement"),
        ("Please answer in English", "explicit output-language requirement"),
        ("中英混合 task without an explicit override", "primary natural language"),
    ],
)
def test_prompt_defines_language_following_without_runtime_translation(
    task_context: str, expected_rule: str,
) -> None:
    # The matrix protects one prompt/context property; Runtime must not inspect these strings.
    prompt = PROMPT.read_text("utf-8")
    assert task_context
    assert expected_rule in prompt
    assert "Preserve project paths, code, commands" in prompt


def test_language_rule_exists_only_in_canonical_prompt_not_runtime() -> None:
    prompt = PROMPT.read_text("utf-8")
    runtime = (Path(tools_knowe.__file__).parent / "runtime.py").read_text("utf-8")
    assert "## Response language" in prompt and "primary natural language" in prompt
    for detector in ("detect_language", "language_regex", "translate_final"):
        assert detector not in runtime
