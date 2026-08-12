# knowe v0.20 — Batch 4：视觉
"""
vision_tools.py — `vision_analyze`。

诚实先行：**Knowe 的默认模型（deepseek-chat）看不了图。**

PRD 说「可使用现有的 DeepSeek API key（如果 DeepSeek 模型支持视觉），
或预留多模态模型扩展点」。答案是「不支持」，所以这个模块的重点其实是
**那个扩展点**，以及**当用户撞上「不支持」时，他会看到什么**。

设计：三个环境变量 KNOWE_VISION_{MODEL,BASE_URL,API_KEY}，默认全部
继承 DeepSeek 那一套。于是：
  · 什么都不配 → 走 DeepSeek → 400 → 一句人话告诉他「配一个能看图的模型」
    （翻译在 aux_client._http_hint，那是唯一知道 HTTP 状态码的地方）
  · 配了 gpt-4o-mini / qwen-vl-max / gemini → 直接就能用，一行代码没改。

「预留扩展点」不是在代码里留个 TODO，是让用户改三个环境变量就能接上任何
OpenAI 兼容的多模态服务 —— 而这恰好是今天所有多模态服务的通用形状。

`image_generate` 按 PRD 不做：Knowe 是开发工具，不是设计工具。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Callable

from . import aux_client
from .agent_runtime import ToolError

log = logging.getLogger("knowe.vision")

_MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: 按**魔数**认类型，不按扩展名 —— 用户把 .jpg 存成 .png 是家常便饭，
#: 而 media_type 报错的时候模型只会看到一句 400。
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def _sniff(raw: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if raw.startswith(magic):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def build_image_part(image_ref: str, resolve_local: Callable[[str], Path]) -> dict[str, Any]:
    """
    把 image_path 变成一个 image_url 内容块。

    URL → 原样交给模型服务（让它自己去抓，省一次下载）。
    本地路径 → 必须过 `resolve_local`（调用方给的沙箱解析器）。

    ★ 这里绝不自己拼路径：沙箱是 tools_knowe 的职责，这个模块只认「解析器」。
      少一个绕过沙箱的机会，就少一次事故。
    """
    ref = (image_ref or "").strip()
    if not ref:
        raise ToolError("image_path 不能为空——传项目内的相对路径，或者一个 http/https 图片地址")

    low = ref.lower()
    if low.startswith(("http://", "https://")):
        return {"type": "image_url", "image_url": {"url": ref}}
    if low.startswith("file://") or low.startswith("data:"):
        raise ToolError("image_path 只支持项目内相对路径或 http/https 地址")

    path = resolve_local(ref)
    if not path.is_file():
        raise ToolError(f"图片不存在：{ref}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ToolError(f"读不了图片：{exc}") from None
    if size > _MAX_IMAGE_BYTES:
        raise ToolError(
            f"图片 {size // 1024 // 1024}MB，超过 {_MAX_IMAGE_BYTES // 1024 // 1024}MB 上限。"
            "请先用 terminal 压缩或缩放（如 sips / ffmpeg / PIL）再分析。"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ToolError(f"读不了图片：{exc}") from None

    mime = _sniff(raw)
    if mime is None:
        raise ToolError(f"认不出这是什么图片格式：{ref}（支持 png / jpeg / gif / webp / bmp / tiff）")
    b64 = base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


#: [v0.34 修复D] 已知**看不了图**的纯文本模型子串。命中 → 直接给一句可执行的
#:   ToolError，而不是烧一次 API 调用换回一段可能被模型幻觉出来的「描述」。
#:   Worker 的「截图+vision 验证」在这类模型上物理上做不到，正确姿势是回去重读文件。
_TEXT_ONLY_VISION_MODELS = ("deepseek-chat", "deepseek-reasoner", "deepseek-v3", "deepseek-r1")


def _is_text_only_vision_model(model: str) -> bool:
    m = (model or "").lower()
    return any(sub in m for sub in _TEXT_ONLY_VISION_MODELS)


async def analyze(
    image_ref: str,
    prompt: str,
    *,
    resolve_local: Callable[[str], Path],
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = 900,
    timeout_s: float = 60.0,
) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ToolError("prompt 不能为空——说清楚你想从这张图里知道什么")
    # Missing credentials are a deployment/configuration error independent of model
    # capability.  Report that first so operators receive the actionable prerequisite
    # instead of a misleading model-capability diagnosis.
    if not isinstance(api_key, str) or not api_key.strip():
        raise ToolError(
            "视觉分析缺少 API key。请配置 KNOWE_VISION_API_KEY（或对应服务的 API key），"
            "并同时确认 KNOWE_VISION_MODEL/BASE_URL 指向可接收图片的多模态模型。"
        )
    # [v0.34 修复D] 视觉模型是纯文本模型 → 当场诚实报错，别让它幻觉一段「看到了什么」。
    #   这正是「反复截图确认却全是假的」的模型层根因：一个看不了图的模型被要求看图，
    #   只能编。给一条能立刻照做的换路指令，而不是一段假描述。
    if _is_text_only_vision_model(model):
        raise ToolError(
            f"当前视觉模型是 {model}，它**看不了图片**，无法用它验证截图。"
            "不要据此声称『已截图验证/页面正常显示』。改用可靠的文本核验："
            "① safe_read_file 重新读回目标位置，确认内容真的在；"
            "② 需要看渲染效果时，用 browser_evaluate 读元素的 computed style / "
            "getBoundingClientRect（宽高是否为 0、是否 display:none / opacity:0）。"
            "要真正看图，请给管理员配置一个多模态模型"
            "（环境变量 KNOWE_VISION_MODEL/BASE_URL/API_KEY，如 gpt-4o-mini、qwen-vl-max）。"
        )
    part = build_image_part(image_ref, resolve_local)
    messages = [{
        "role": "user",
        "content": [part, {"type": "text", "text": prompt.strip()}],
    }]
    text = await aux_client.chat(
        messages, api_key=api_key, base_url=base_url, model=model,
        timeout_s=timeout_s,
        what="视觉分析",
    )
    if not text:
        raise ToolError("视觉模型没有返回内容——可能是模型不支持图片输入，或者图片太大")
    return text


__all__ = ["analyze", "build_image_part"]
