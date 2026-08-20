# knowe v0.20 — Batch 4：辅助 LLM 调用
"""
aux_client.py — 一个「打 OpenAI 兼容 /chat/completions」的小客户端。

Batch 4 里有两个地方要请另一个模型帮个忙：
  · `web_extract(summarize=true)` —— 一篇 8000 字的文档，只要那三行答案；
  · `vision_analyze`             —— 主模型（DeepSeek）看不了图，得请个能看的。

memory_manager.py 里已经有一份**一模一样**的 httpx 调用逻辑
（`MemoryManager._call_llm`）。我没有去动它——它是私有方法、有自己的降级语义，
而「不改现有工具的行为」是这一批的硬约束。所以这里是第二份，代价是几十行重复；
将来谁做清理，把 memory_manager 接到这里就行，签名是照着它写的。

**降级是这里的主线，不是补丁**：没装 httpx → 退回 stdlib 的 urllib（丢到线程里跑）；
没配 key → 一句人话；模型不支持图片 → 一句人话 + 该配什么。
任何一种失败都不许把 Agent 的回合带走。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .agent_runtime import ToolError

log = logging.getLogger("knowe.aux")

_UA = "Knowe/0.20 (+https://github.com/knowe)"


async def chat(
    messages: list[dict[str, Any]],
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout_s: float = 40.0,
    what: str = "辅助模型",
    transport: str = "openai_chat",
) -> str:
    """
    打一次对话补全，把文本拿回来。失败一律 ToolError（中文人话）。

    ``transport`` 决定协议：openai_chat/codex_responses 走 ``/chat/completions``；
    anthropic_messages 走 ``/v1/messages``（x-api-key + anthropic-version）。
    """
    if not api_key:
        raise ToolError(f"{what}需要配置 API key（环境变量 DEEPSEEK_API_KEY 或对应的专用变量）")
    if not (base_url or "").strip():
        # [v0.44.2 Bug1] base_url 为空时，下面的 rstrip('/') + '/chat/completions' 会拼出一个
        #   没有协议头的相对路径 → httpx/urllib 直接报「missing http://」这类看不懂的底层错。
        #   和主模型一样，缺接入点就给一句人话，指向设置面板（辅助模型默认跟随主模型服务商，
        #   所以通常是主模型还没配好）。
        raise ToolError(
            f"{what}还没有可用的接入点（base_url 为空）——请到「设置 → 模型与提供方」"
            f"配置主模型（辅助模型默认跟随主模型的服务商）。"
        )

    is_anthropic = (transport or "openai_chat") == "anthropic_messages"
    if is_anthropic:
        from knowe_core.anthropic_codec import (
            build_headers as _anth_headers,
            encode_request as _anth_encode,
            resolve_endpoint as _anth_endpoint,
        )

        url = _anth_endpoint(base_url)
        payload = _anth_encode(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        headers = _anth_headers(api_key)
        headers["User-Agent"] = _UA
    else:
        url = base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }

    try:
        import httpx
    except ImportError:
        return _parse_aux(await _urllib_post(url, payload, headers, timeout_s), what, model, is_anthropic)

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as cli:
            r = await cli.post(url, headers=headers, json=payload)
            if r.status_code >= 400:
                raise ToolError(_http_hint(r.status_code, r.text, what, model))
            return _parse_aux(r.json(), what, model, is_anthropic)
    except ToolError:
        raise
    except Exception as exc:                       # httpx.TimeoutException / ConnectError / …
        raise ToolError(f"{what}调用失败：{type(exc).__name__}: {exc}") from None


async def _urllib_post(url: str, payload: dict[str, Any], headers: dict[str, str],
                       timeout_s: float) -> dict[str, Any]:
    """没装 httpx 也能用 —— 阻塞调用丢进线程，绝不占着事件循环。"""
    import asyncio

    def _post() -> dict[str, Any]:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:600]
            raise ToolError(_http_hint(exc.code, body, "辅助模型", str(payload.get("model")))) from None
        except Exception as exc:
            raise ToolError(f"辅助模型调用失败：{type(exc).__name__}: {exc}") from None

    return await asyncio.to_thread(_post)


def _parse_aux(data: dict[str, Any], what: str, model: str, is_anthropic: bool = False) -> str:
    try:
        if is_anthropic:
            # anthropic 响应：content 是 blocks 列表，取 text 块拼起来
            parts: list[str] = []
            for block in data.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            text = "".join(parts)
            if not text and isinstance(data.get("content") or [], list) and data.get("content"):
                # 可能是 tool_use 或无 text 的响应——不猜，交给上层
                text = ""
            if text == "" and not data.get("content"):
                raise KeyError("no content")
            return (text or "").strip()
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ToolError(f"{what}返回了看不懂的结构：{str(data)[:200]}") from None
    return (text or "").strip()


def _http_hint(status: int, body: str, what: str, model: str) -> str:
    """
    把 HTTP 错误翻译成「用户/模型能照着做点什么」的话。

    ★ 最重要的是 400：DeepSeek 的 deepseek-chat **不支持图片输入**，
      而它正是 Knowe 的默认模型。用户一调 vision_analyze 就会撞上这个 400。
      如果只回一句「HTTP 400: invalid request」，用户会以为是 Knowe 坏了。
      得直接告诉他：不是坏了，是这个模型看不了图，要看图请配一个能看图的。
    """
    snippet = (body or "").strip().replace("\n", " ")[:400]
    low = snippet.lower()
    if status in (401, 403):
        return f"{what} API key 无效或没有权限（HTTP {status}）：{snippet}"
    if status == 402:
        return f"{what}账户余额不足（HTTP 402）：{snippet}"
    if status == 429:
        return f"{what}被限流了（HTTP 429），稍后再试：{snippet}"
    if status == 400 and any(k in low for k in
                             ("image", "vision", "multimodal", "content type", "image_url")):
        return (
            f"当前模型 `{model}` 不支持图片输入（HTTP 400：{snippet}）。\n"
            "这不是故障：DeepSeek 的对话模型是纯文本的。要用视觉分析，请让用户配置一个"
            "多模态模型——设置 KNOWE_VISION_MODEL / KNOWE_VISION_BASE_URL / KNOWE_VISION_API_KEY"
            "（指向任何 OpenAI 兼容的多模态服务即可，如 gpt-4o-mini、qwen-vl-max、gemini 系列）。"
        )
    if status >= 500:
        return f"{what}服务端错误（HTTP {status}），可以稍后重试：{snippet}"
    return f"{what}调用被拒绝（HTTP {status}）：{snippet}"


__all__ = ["chat"]
