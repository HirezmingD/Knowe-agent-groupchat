"""Coordinator soul and v2.2 single-Worker-prompt regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

SOULS = Path(__file__).resolve().parents[1] / "backend" / "souls"


def coordinator_soul() -> str:
    return (SOULS / "coordinator.txt").read_text("utf-8")


def test_coordinator_asks_the_right_question() -> None:
    soul = coordinator_soul()
    assert "用户还不知道什么" in soul
    assert "别问自己「他做到了什么」" in soul


def test_coordinator_knows_silence_is_allowed() -> None:
    soul = coordinator_soul()
    assert "NOTHING_TO_ADD" in soul
    assert "这不是偷懒，这是本分" in soul
    assert "沉默比噪音有价值" in soul


def test_coordinator_reads_generated_capability_context() -> None:
    soul = coordinator_soul()
    for tool in (
        "terminal",
        "browser_navigate",
        "web_search",
        "safe_patch",
        "vision_analyze",
        "execute_code",
    ):
        assert tool not in soul
    assert soul.count("【成员能做什么】") >= 2
    assert "你自己没有 ≠ 团队没有" in soul


def test_worker_soul_points_to_the_single_canonical_prompt() -> None:
    path = SOULS / "worker.txt"
    assert path.is_file()
    soul = path.read_text("utf-8")
    assert "worker_prompt.md" in soul
    assert "fixed 19" in soul
    assert "Provider-native tool calls" in soul
    for retired in (
        "ContextBundleV2", "capability lease", "submit_report", "调用 speak",
        "prompt bundle", "surface plan", "result ref",
    ):
        assert retired not in soul


@pytest.mark.parametrize(
    "rule",
    [
        "propose_agents",
        "propose_next",
        "propose_remove_agent",
        "永远不要让用户看到 id",
        "加人是**增量**",
    ],
)
def test_coordinator_keeps_hard_won_rules(rule: str) -> None:
    assert rule in coordinator_soul()


def test_role_catalog_stays_out_of_the_soul() -> None:
    soul = coordinator_soul()
    assert "前端(fe) · 后端(be)" not in soul
    assert "完整的角色目录" in soul
