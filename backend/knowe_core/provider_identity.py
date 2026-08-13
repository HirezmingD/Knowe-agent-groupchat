"""
provider_identity.py — 服务商身份与错误文案的单一真源。

Knowe 早期只有 DeepSeek，一些错误出口把厂商名和环境变量名写死在字符串里。
接入多服务商后，请求本身已经能切换，但异常仍会显示旧厂商，造成“实际请求去哪儿”
与“界面说去哪儿”互相矛盾。本模块只做两件事：

1. 把 provider slug / base_url 解析成人能看懂的服务商名称；
2. 把 HTTP/网络错误翻译成**当前绑定**对应的文案，不再猜成 DeepSeek。

不保存、不打印 API Key；base_url 只用于识别主机名。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from knowe_core.redaction import redact_sensitive_text

_PROVIDER_LABELS: dict[str, str] = {
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "zai": "Z.AI / GLM",
    "kimi-coding": "Kimi / Kimi Coding Plan",
    "kimi-coding-cn": "Kimi / Moonshot (China)",
    "alibaba": "Qwen Cloud",
    "minimax": "MiniMax",
    "minimax-cn": "MiniMax (China)",
    "stepfun": "StepFun Step Plan",
    "tencent-tokenhub": "Tencent TokenHub",
    "xai": "xAI",
    "anthropic": "Anthropic",
    "openai-api": "OpenAI API",
    "gemini": "Google AI Studio",
    "nvidia": "NVIDIA NIM",
    "huggingface": "Hugging Face",
    "novita": "NovitaAI",
    "arcee": "Arcee AI",
    "gmi": "GMI Cloud",
    "copilot": "GitHub Copilot",
    "opencode-zen": "OpenCode Zen",
}

# provider 字段缺失/来自老配置时，用实际接入点再兜一层。
_HOST_LABELS: dict[str, str] = {
    "openrouter.ai": "OpenRouter",
    "api.deepseek.com": "DeepSeek",
    "api.z.ai": "Z.AI / GLM",
    "open.bigmodel.cn": "Z.AI / GLM",
    "api.moonshot.ai": "Kimi / Moonshot",
    "api.moonshot.cn": "Kimi / Moonshot (China)",
    "dashscope-intl.aliyuncs.com": "Qwen Cloud",
    "dashscope.aliyuncs.com": "Qwen Cloud",
    "api.minimax.io": "MiniMax",
    "api.minimaxi.com": "MiniMax (China)",
    "api.stepfun.com": "StepFun",
    "api.x.ai": "xAI",
    "api.anthropic.com": "Anthropic",
    "api.openai.com": "OpenAI API",
    "generativelanguage.googleapis.com": "Google AI Studio",
    "integrate.api.nvidia.com": "NVIDIA NIM",
    "api-inference.huggingface.co": "Hugging Face",
    "api.novita.ai": "NovitaAI",
    "api.arcee.ai": "Arcee AI",
    "api.gmicloud.ai": "GMI Cloud",
    "api.githubcopilot.com": "GitHub Copilot",
}


def provider_display_name(provider: str | None = None, base_url: str | None = None) -> str:
    """返回适合界面/日志展示的服务商名；识别不了时仍不臆测成某个厂商。"""
    slug = str(provider or "").strip().lower()
    if slug in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[slug]

    raw_base = str(base_url or "").strip()
    if raw_base:
        try:
            host = (urlsplit(raw_base).hostname or "").lower()
        except ValueError:
            host = ""
        if host in _HOST_LABELS:
            return _HOST_LABELS[host]
        if host:
            return host

    # 自定义/未来 provider 至少显示它自己的 slug，绝不回落成 DeepSeek。
    return str(provider or "").strip() or "当前模型服务商"


def provider_target(
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> str:
    """服务商 + 模型的人类可读目标，例如 ``Z.AI / GLM（glm-5.1）``。"""
    label = provider_display_name(provider, base_url)
    model_name = str(model or "").strip()
    return f"{label}（{model_name}）" if model_name else label


def _clean_response_detail(
    body: Any,
    *,
    limit: int = 180,
    secrets: tuple[str, ...] = (),
) -> str:
    """从厂商响应里提炼一小段可读说明，避免把整坨 JSON 直接甩给用户。"""
    text = str(body or "").strip()
    if not text:
        return ""

    payload: Any = None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        # 老版本常把厂商名/HTTP 状态包在 JSON 前面，例如：
        #   DeepSeek 返回 401：{"error":{"message":"token expired"}}
        # 只取后面的 JSON，既保留真正的服务端原因，也不把旧厂商名重新带进新文案。
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if 0 <= first_brace < last_brace:
            try:
                payload = json.loads(text[first_brace:last_brace + 1])
            except (TypeError, ValueError):
                payload = None

    if isinstance(payload, dict):
        candidates: list[Any] = []
        err = payload.get("error")
        if isinstance(err, dict):
            candidates.extend((err.get("message"), err.get("detail"), err.get("code")))
        elif err:
            candidates.append(err)
        candidates.extend((payload.get("message"), payload.get("detail")))
        for item in candidates:
            if item is not None and str(item).strip():
                text = str(item).strip()
                break

    text = re.sub(r"\s+", " ", text).strip()
    return redact_sensitive_text(text, secrets=secrets, limit=limit)


def _detail_for_rebuilt_message(error_text: str) -> str:
    """
    给“按当前绑定重建”的错误文案挑一段安全 detail。

    旧错误已经被翻译成 ``DeepSeek API 认证失败`` 之类时，它不是服务端原话，不能再
    作为 detail 附回去；否则主句虽改成 Z.AI，尾巴又会出现 DEEPSEEK_API_KEY。若旧串里
    带了 JSON，则保留 JSON，让 ``_clean_response_detail`` 提炼真正的厂商说明。
    """
    text = str(error_text or "").strip()
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if 0 <= first_brace < last_brace:
        return text[first_brace:last_brace + 1]

    if re.search(
        r"deepseek|DEEPSEEK_API_KEY|认证失败|余额不足|拒绝请求|被限流|触发限流|意外状态码",
        text,
        re.IGNORECASE,
    ):
        return ""
    return text


def http_status_error_message(
    status_code: int,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    response_body: Any = "",
    secrets: tuple[str, ...] = (),
) -> str:
    """把一次 provider HTTP 失败翻译成绑定感知、可执行的中文说明。"""
    target = provider_target(provider, base_url, model)
    detail = _clean_response_detail(response_body, secrets=secrets)
    detail_suffix = f" 服务端说明：{detail}" if detail else ""

    if status_code == 401:
        return (
            f"{target} API 认证失败（HTTP 401）——请检查「设置 → 模型与提供方」中"
            f"当前绑定的 API Key 是否正确、未过期。{detail_suffix}"
        ).rstrip()
    if status_code == 402:
        return (
            f"{target} API 余额或额度不足（HTTP 402）——请到该服务商控制台检查余额、"
            f"套餐或调用额度。{detail_suffix}"
        ).rstrip()
    if status_code == 403:
        return (
            f"{target} API 拒绝请求（HTTP 403）——请检查 API Key 权限、该模型的访问权限"
            f"以及账号状态。{detail_suffix}"
        ).rstrip()
    if status_code == 429:
        return (
            f"{target} API 触发限流（HTTP 429）——请稍后重试，或检查调用频率与配额。"
            f"{detail_suffix}"
        ).rstrip()
    if status_code >= 500:
        return (
            f"{target} 服务暂时异常（HTTP {status_code}）——请稍后重试。{detail_suffix}"
        ).rstrip()

    return (
        f"{target} 返回 HTTP {status_code}。{detail_suffix or ' 请检查模型名、接入点与请求协议。'}"
    ).rstrip()


def humanize_provider_error(
    error_text: str,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """
    把任意 provider 异常归类为 ``(去重键, 用户文案)``。

    若底层已经用**当前绑定**生成了完整人话，原样保留；若收到的是旧版写死的
    ``DeepSeek / DEEPSEEK_API_KEY`` 文案或原始 401/402 串，则按当前绑定重新生成，
    从而保证模型切换后错误不会继续指向旧厂商。
    """
    e = str(error_text or "").strip()
    lower = e.lower()
    target = provider_target(provider, base_url, model)
    rebuilt_detail = _detail_for_rebuilt_message(e)

    def already_humanized(markers: tuple[str, ...]) -> bool:
        return target in e and any(marker in e for marker in markers)

    if "余额不足" in e or "insufficient balance" in lower or re.search(r"\b402\b", e):
        if already_humanized(("余额", "额度")):
            return "balance", e[:320]
        return "balance", http_status_error_message(
            402, provider=provider, base_url=base_url, model=model,
            response_body=rebuilt_detail,
        )

    if ("认证失败" in e or "unauthorized" in lower or "token expired" in lower
            or "incorrect token" in lower or re.search(r"\b401\b", e)):
        if already_humanized(("认证失败",)):
            return "auth", e[:320]
        return "auth", http_status_error_message(
            401, provider=provider, base_url=base_url, model=model,
            response_body=rebuilt_detail,
        )

    if "拒绝请求" in e or "forbidden" in lower or re.search(r"\b403\b", e):
        if already_humanized(("拒绝请求",)):
            return "forbidden", e[:320]
        return "forbidden", http_status_error_message(
            403, provider=provider, base_url=base_url, model=model,
            response_body=rebuilt_detail,
        )

    if "限流" in e or "rate limit" in lower or re.search(r"\b429\b", e):
        if already_humanized(("限流",)):
            return "rate", e[:320]
        return "rate", http_status_error_message(
            429, provider=provider, base_url=base_url, model=model,
            response_body=rebuilt_detail,
        )

    if "超时" in e or "timeout" in lower or "timed out" in lower:
        return "timeout", f"调用 {target} 超时了——网络或服务暂时不稳，请再发一次。"

    if (
        "连不上" in e
        or "连接" in e
        or "provider" in lower
        or "connect" in lower
        or "connection" in lower
        or "unreachable" in lower
        or "econnrefused" in lower
    ):
        return (
            "provider",
            f"连接 {target} 失败。请依次检查：① 服务商余额/额度；② 当前绑定的 API Key；"
            "③ 本机网络与接入点；④ 服务商是否临时故障。排查后直接继续对话即可，不用重启。",
        )

    return "other", (e[:160] if e else f"{target} 调用失败（未返回具体原因）")


__all__ = [
    "provider_display_name",
    "provider_target",
    "http_status_error_message",
    "humanize_provider_error",
]
