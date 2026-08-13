"""v2.2 single Worker prompt and fixed tool-boundary tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend import tools_knowe
from backend.prompt_resolver import resolve_prompt_path
from backend.runtime import WORKER_TOOL_NAMES

PROMPT = Path(tools_knowe.__file__).with_name("worker_prompt.md")
SOUL = Path(tools_knowe.__file__).parent / "souls" / "worker.txt"
SPEC = Path(tools_knowe.__file__).with_name("KnoweBackend.spec")


def localized_prompt(lang: str) -> tuple[Path, str]:
    path = resolve_prompt_path("worker_prompt.md", lang=lang)
    assert path is not None and path.is_file()
    return path, path.read_text("utf-8")


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


def test_language_resolved_worker_prompt_family_is_packaged() -> None:
    assert PROMPT.is_file()
    backend_root = Path(tools_knowe.__file__).resolve().parents[1]
    assert not (backend_root / "knowe_prompts").exists()
    assert not (Path(tools_knowe.__file__).parent / "knowe_prompts").exists()
    for lang in ("zh", "en"):
        path, _ = localized_prompt(lang)
        assert path == Path(tools_knowe.__file__).parent / "prompts" / lang / "worker_prompt.md"
    # PyInstaller recursively ships the directory selected by prompt_resolver.
    assert '("prompts", "backend/prompts")' in SPEC.read_text("utf-8")


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
    ("lang", "language_rule", "explicit_rule", "preserve_rule"),
    [
        ("zh", "否则使用简体中文", "明确指定的输出语言", "保留项目路径、代码、命令"),
        ("en", "Otherwise, reply in English", "explicit output-language requirement", "Preserve project paths, code, commands"),
    ],
)
def test_prompt_follows_the_resolved_active_language_without_runtime_translation(
    lang: str,
    language_rule: str,
    explicit_rule: str,
    preserve_rule: str,
) -> None:
    _, prompt = localized_prompt(lang)
    assert language_rule in prompt
    assert explicit_rule in prompt
    assert preserve_rule in prompt


def test_language_rules_and_security_contract_exist_in_both_resolved_prompts_not_runtime() -> None:
    _, zh = localized_prompt("zh")
    _, en = localized_prompt("en")
    runtime = (Path(tools_knowe.__file__).parent / "runtime.py").read_text("utf-8")
    assert "## 回复语言" in zh and "系统当前启用的语言" in zh
    assert "## Response language" in en and "active system language" in en
    for prompt in (zh, en):
        for invariant in ("<tool_call>", "19", "offset", "limit", "safe_bash", "SHA-256"):
            assert invariant in prompt
    for detector in ("detect_language", "language_regex", "translate_final"):
        assert detector not in runtime
