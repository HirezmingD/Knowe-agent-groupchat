"""[v0.34 修复D] 视觉核验在纯文本模型上诚实报错，而不是幻觉一段「看到了什么」。

对应审计报告：Claude 方案 修复4（核验环节）。

跑法：  pytest tests/test_vision_guard.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.vision_tools as vt
from backend.agent_runtime import ToolError


def test_text_only_model_detection():
    assert vt._is_text_only_vision_model("deepseek-chat")
    assert vt._is_text_only_vision_model("DeepSeek-Reasoner")
    assert not vt._is_text_only_vision_model("gpt-4o-mini")
    assert not vt._is_text_only_vision_model("qwen-vl-max")


@pytest.mark.asyncio
async def test_analyze_refuses_on_text_only_model():
    """deepseek-chat 看不了图 → analyze 直接 ToolError，给出可执行的换路指令，
    绝不烧一次 API 调用换回一段可能是幻觉的『描述』。"""
    with pytest.raises(ToolError) as ei:
        await vt.analyze(
            "screenshots/chapter2.png", "第二章正文是否可见？",
            resolve_local=lambda r: Path(r),
            api_key="k", base_url="https://api.deepseek.com", model="deepseek-chat",
        )
    msg = str(ei.value)
    assert "看不了图" in msg
    assert "safe_read_file" in msg or "browser_evaluate" in msg   # 指了可靠的换路


@pytest.mark.asyncio
async def test_analyze_proceeds_past_guard_on_vision_model(tmp_path):
    """配了真视觉模型时不拦——越过守卫后按正常路径走（这里因无真实图/网络，
    会在后续读图/网络处失败，但**不是**被守卫拦下）。"""
    with pytest.raises(ToolError) as ei:
        await vt.analyze(
            "nope.png", "看图",
            resolve_local=lambda r: tmp_path / r,
            api_key="k", base_url="https://api.openai.com", model="gpt-4o-mini",
        )
    # 越过了纯文本守卫 → 报的是「图片不存在」，而不是「看不了图」
    assert "看不了图" not in str(ei.value)
