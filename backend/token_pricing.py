"""Read-only model pricing used by the project token monitor.

Rates are per one million tokens.  A model may carry multiple currency entries
(``CNY`` / ``USD``); callers pick the currency that matches the provider endpoint.
Model lookup is deliberately exact: provider aliases and dated model IDs are listed
explicitly instead of being guessed with fuzzy matching.  Unknown prices remain
visible in the table with ``source='unknown'`` and ``None`` rates so accounting
never fabricates an estimate.

Cache-hit input rates: ``None`` means the provider has no cache tier and the
standard (miss) input rate applies to every input token.

M2 (2026-08-01): full catalog pricing from 价格表_全量.md (163 models priced,
62 unpriced).  Tiered models (input-length pricing) live in ``_TIERS`` and are
selected by per-turn input total in ``estimate_cost``; the ``_PRICING`` entry
keeps the default (first) tier for display.  Same-model cross-provider price
conflicts (e.g. reseller CNY rates on Bailian) resolve to the official direct
rate.  See 审计_M2价格表落地差距.md and PRD FR2.

DeepSeek 峰谷定价 (2026-08-14 采集, 官方 2026-08-17 生效): 高峰时段
(北京 9:00-12:00 / 14:00-18:00 = UTC 01:00-04:00 / 06:00-10:00) 用
``_PEAK_RATES`` 价, 其余时段用 ``_RAW`` 内空闲价.  ``get_model_pricing`` /
``estimate_cost`` / ``pricing_payload`` 按调用时刻自动选档 (可选 ``now``
参数注入固定时间供测试).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Final, Mapping


@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_cost_per_1M: float | None          # 未命中输入（标准输入）
    output_cost_per_1M: float | None
    source: str
    currency: str = "USD"
    cache_hit_input_cost_per_1M: float | None = None  # None = 与未命中输入同价

    @property
    def known(self) -> bool:
        return (
            self.input_cost_per_1M is not None
            and self.output_cost_per_1M is not None
            and self.source != "unknown"
        )


_UNKNOWN = ModelPricing(None, None, "unknown")


# ── 数据来源（按供应商分组，M2 全量价格采集于 2026-08-01） ──────────
_SOURCES: dict[str, str] = {
    "openai-api": "https://platform.openai.com/docs/pricing",
    "anthropic": "https://docs.anthropic.com/en/docs/about-claude/pricing",
    "gemini": "https://ai.google.dev/pricing",
    "zai": "https://docs.z.ai（CNY 见 bigmodel.cn）",
    "kimi": "https://platform.moonshot.cn/docs/pricing/chat",
    "deepseek": "https://api-docs.deepseek.com/quick_start/pricing",
    "alibaba": "https://bailian.console.aliyun.com + qwencloud.com",
    "minimax": "https://platform.minimax.io",
    "minimax-cn": "https://platform.minimaxi.com",
    "stepfun": "https://platform.stepfun.com",
    "tencent": "https://cloud.tencent.com",
    "xai": "https://docs.x.ai/developers/pricing",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "novita": "https://novita.ai/zh/pricing",
    "arcee": "https://docs.arcee.ai/get-started/pricing",
    "nvidia": "openrouter 同源价（nemotron super）",
    "alias": "价格表同源映射（2026-08-01）",
}

# ── 紧凑原始数据：provider → model → currency → (hit, miss, out) ──────
#   hit=None  = 无缓存档（命中按未命中价）；0.0 = 免费
#   转售 CNY 冲突（kimi-k2.5/glm-5/glm-4.7/MiniMax-M2.5）按官方直营端点价
_RAW: dict[str, dict[str, dict[str, tuple[float | None, float | None, float | None]]]] = {
    "openai-api": {
        "gpt-5.6-sol":  {"USD": (0.50, 5.00, 30.00)},
        "gpt-5.6-terra": {"USD": (0.20, 2.00, 12.00)},
        "gpt-5.6-luna":  {"USD": (0.02, 0.20, 1.20)},
        "gpt-5.5":       {"USD": (0.50, 5.00, 30.00)},
        "gpt-5.5-pro":   {"USD": (None, 30.00, 180.00)},
        "gpt-5.4":       {"USD": (0.25, 2.50, 15.00)},
        "gpt-5.4-pro":   {"USD": (None, 30.00, 180.00)},
        "gpt-5.4-mini":  {"USD": (0.075, 0.75, 4.50)},
        "gpt-5.4-nano":  {"USD": (0.02, 0.20, 1.25)},
        "gpt-5-mini":    {"USD": (0.025, 0.25, 2.00)},
        "gpt-4.1":       {"USD": (0.50, 2.00, 8.00)},
        "gpt-4o":        {"USD": (1.25, 2.50, 10.00)},
        "gpt-4o-mini":   {"USD": (0.075, 0.15, 0.60)},
        # 留档备用（目录外/官方查无，仅备查）：gpt-5.2 $1.75/0.175/14、
        # gpt-5.1 $1.25/0.125/10、gpt-5 $1.25/0.125/10、gpt-4.1-mini $0.4/0.1/1.6
        # 暂无价格表：gpt-5.6-sol-pro / terra-pro / luna-pro / gpt-5.3-codex
    },
    "anthropic": {
        "claude-fable-5":            {"USD": (1.00, 10.00, 50.00)},
        "claude-sonnet-5":           {"USD": (0.20, 2.00, 10.00)},
        "claude-opus-4-8":           {"USD": (0.50, 5.00, 25.00)},
        "claude-opus-4-7":           {"USD": (0.50, 5.00, 25.00)},
        "claude-opus-4-6":           {"USD": (0.50, 5.00, 25.00)},
        "claude-sonnet-4-6":         {"USD": (0.30, 3.00, 15.00)},
        "claude-opus-4-5-20251101":  {"USD": (0.50, 5.00, 25.00)},
        "claude-sonnet-4-5-20250929": {"USD": (0.30, 3.00, 15.00)},
        "claude-opus-4-20250514":    {"USD": (1.50, 15.00, 75.00)},
        "claude-sonnet-4-20250514":  {"USD": (0.30, 3.00, 15.00)},
        "claude-haiku-4-5-20251001": {"USD": (0.10, 1.00, 5.00)},
    },
    "gemini": {
        "gemini-3.1-pro-preview": {"USD": (0.20, 2.00, 12.00)},  # 分档见 _TIERS
        "gemini-3.6-flash":       {"USD": (0.15, 1.50, 7.50)},
        # 暂无价格表：gemini-3-pro-preview / gemini-3.1-flash-lite-preview
    },
    "zai": {
        # USD 国际版（docs.z.ai）
        "glm-5.2":      {"USD": (0.26, 1.40, 4.40), "CNY": (2.00, 8.00, 28.00)},
        "glm-5.1":      {"USD": (0.26, 1.40, 4.40), "CNY": (1.30, 6.00, 24.00)},  # CNY 分档
        "glm-5":        {"USD": (0.20, 1.00, 3.20), "CNY": (1.00, 4.00, 18.00)},  # CNY 分档
        "glm-5-turbo":  {"USD": (0.24, 1.20, 4.00), "CNY": (1.20, 5.00, 22.00)},  # CNY 分档
        "glm-5v-turbo": {"USD": (0.24, 1.20, 4.00), "CNY": (1.20, 5.00, 22.00)},  # CNY 分档
        "glm-4.7":      {"USD": (0.11, 0.60, 2.20), "CNY": (0.40, 2.00, 8.00)},   # CNY 分档（双维）
        "glm-4.5":      {"USD": (0.11, 0.60, 2.20)},
        "glm-4.5-flash": {"USD": (0.0, 0.0, 0.0)},  # 免费；国内页无 API 推理价
    },
    "kimi": {
        "kimi-k3":                  {"CNY": (2.00, 20.00, 100.00), "USD": (0.30, 3.00, 15.00)},
        "kimi-k2.7-code":           {"CNY": (1.30, 6.50, 27.00), "USD": (0.19, 0.95, 4.00)},
        "kimi-k2.7-code-highspeed": {"CNY": (2.60, 13.00, 54.00), "USD": (0.38, 1.90, 8.00)},
        "kimi-k2.6":                {"CNY": (1.10, 6.50, 27.00), "USD": (0.16, 0.95, 4.00)},
        "kimi-k2.5":                {"CNY": (0.70, 4.00, 21.00), "USD": (0.10, 0.60, 3.00)},
        # 暂无价格表（订阅/旧模型）：kimi-for-coding / -highspeed / kimi-k2-thinking /
        # k2-thinking-turbo / k2-turbo-preview / k2-0905-preview
    },
    "deepseek": {
        # [2026-08-14] 官方 2026-08-17 起峰谷定价，此处存空闲档（高峰=空闲×2）：
        # 高峰档见 _PEAK_RATES，get_model_pricing 按请求时刻自动选档。
        # 旧价（8/17 前）留档：flash 0.02/1.00/2.00、pro 0.025/3.00/6.00（CNY）。
        "deepseek-v4-pro":  {"CNY": (0.15, 4.50, 13.50), "USD": (0.022, 0.66, 1.98)},
        "deepseek-v4-flash": {"CNY": (0.05, 1.50, 4.50), "USD": (0.007, 0.22, 0.66)},
    },
    "alibaba": {
        # CNY = 百炼促销价（当前支付价）；USD = Qwen marketplace 区间取下沿（D5）
        "qwen3.8-max":    {"CNY": (1.50, 12.00, 36.00), "USD": (None, None, None)},  # [v1.0.24.2] 2026-08-06 官方：输入12/输出36/缓存命中1.5 ¥/M；USD 官方页未公布
        "qwen3.7-max":     {"CNY": (1.20, 6.00, 18.00), "USD": (None, 1.25, 3.75)},
        "qwen3.7-plus":    {"CNY": (0.32, 1.60, 6.40), "USD": (None, 0.32, 1.28)},
        "qwen3.6-plus":    {"CNY": (None, 2.00, 12.00), "USD": (None, 0.50, 3.00)},
        "qwen3.5-plus":    {"CNY": (None, 0.80, 4.80), "USD": (None, 0.40, 2.40)},
        "qwen3-coder-plus": {"CNY": (0.80, 4.00, 16.00), "USD": (None, 1.00, 5.00)},
        "qwen3-coder-next": {"USD": (None, 0.30, 1.50)},  # 百炼无 → CNY 暂无价格表
        # 转售模型 CNY 冲突按官方直营价（kimi-k2.5/glm-5/glm-4.7/MiniMax-M2.5 见官方组）
    },
    "minimax": {
        "MiniMax-M3":   {"USD": (0.06, 0.30, 1.20)},   # ≤512K 标准档（>512K 档数据存疑，备注保留）
        "MiniMax-M2.7": {"USD": (0.06, 0.30, 1.20)},
        "MiniMax-M2.5": {"USD": (0.03, 0.30, 1.20)},
        "MiniMax-M2.1": {"USD": (0.03, 0.30, 1.20)},
        "MiniMax-M2":   {"USD": (0.03, 0.30, 1.20)},
    },
    "minimax-cn": {
        "MiniMax-M3":   {"CNY": (0.42, 2.10, 8.40)},
        "MiniMax-M2.7": {"CNY": (0.42, 2.10, 8.40)},
        "MiniMax-M2.5": {"CNY": (0.21, 2.10, 8.40)},
        "MiniMax-M2.1": {"CNY": (0.21, 2.10, 8.40)},
        "MiniMax-M2":   {"CNY": (0.21, 2.10, 8.40)},
    },
    "stepfun": {
        "step-3.5-flash":      {"CNY": (0.14, 0.70, 2.10)},
        "step-3.5-flash-2603": {"CNY": (0.14, 0.70, 2.10)},
    },
    "tencent": {
        "hy3-preview": {"CNY": (0.40, 1.20, 4.00)},  # 三档见 _TIERS
    },
    "xai": {
        "grok-build-0.1":             {"USD": (0.20, 1.00, 2.00)},  # 长短上下文分档
        "grok-4.6":                   {"USD": (0.50, 2.00, 6.00)},  # [2026-08-14] 官方 <200K 档；分档见 _TIERS
        "grok-4.5":                   {"USD": (0.30, 2.00, 6.00)},
        "grok-4.3":                   {"USD": (0.20, 1.25, 2.50)},
        "grok-4.20-0309-reasoning":   {"USD": (0.20, 1.25, 2.50)},
        "grok-4.20-0309-non-reasoning": {"USD": (0.20, 1.25, 2.50)},
        "grok-4.20-multi-agent-0309": {"USD": (0.20, 1.25, 2.50)},
    },
    "openrouter": {
        "anthropic/claude-fable-5":         {"USD": (1.00, 10.00, 50.00)},
        "anthropic/claude-opus-5":          {"USD": (0.50, 5.00, 25.00)},
        "anthropic/claude-opus-5-fast":     {"USD": (1.00, 10.00, 50.00)},
        "anthropic/claude-opus-4.8":        {"USD": (0.50, 5.00, 25.00)},
        "anthropic/claude-opus-4.8-fast":   {"USD": (1.00, 10.00, 50.00)},
        "anthropic/claude-sonnet-5":        {"USD": (0.20, 2.00, 10.00)},
        "anthropic/claude-haiku-4.5":       {"USD": (0.10, 1.00, 5.00)},
        "openai/gpt-5.6-sol":               {"USD": (0.50, 5.00, 30.00)},
        "openai/gpt-5.6-sol-pro":           {"USD": (0.50, 5.00, 30.00)},
        "openai/gpt-5.6-terra":             {"USD": (0.10, 1.00, 6.00)},
        "openai/gpt-5.6-terra-pro":         {"USD": (0.10, 1.00, 6.00)},
        "openai/gpt-5.6-luna":              {"USD": (0.01, 0.10, 0.60)},
        "openai/gpt-5.6-luna-pro":          {"USD": (0.01, 0.10, 0.60)},
        "openai/gpt-5.5":                   {"USD": (0.50, 5.00, 30.00)},
        "openai/gpt-5.5-pro":               {"USD": (None, 30.00, 180.00)},
        "openai/gpt-5.4-mini":              {"USD": (0.075, 0.75, 4.50)},
        "google/gemini-3.1-pro-preview":    {"USD": (0.20, 2.00, 12.00)},
        "google/gemini-3.6-flash":          {"USD": (0.15, 1.50, 7.50)},
        "x-ai/grok-4.5":                    {"USD": (0.30, 2.00, 6.00)},
        "deepseek/deepseek-v4-pro":         {"USD": (0.0036, 0.435, 0.87)},
        "deepseek/deepseek-v4-flash":       {"USD": (0.028, 0.14, 0.28)},
        "x-ai/grok-4.6":                    {"USD": (0.50, 2.00, 6.00)},  # [2026-08-14] OR 实时 $2/$6 与官方一致；缓存取官方 0.50
        "x-ai/grok-4.5":                    {"USD": (0.30, 2.00, 6.00)},
        "qwen/qwen3.8-max":                 {"USD": (0.40, 2.00, 6.00)},   # [v1.0.24.2] OpenRouter 2026-08-06 实时：$2/$6 per M，缓存=输入×0.2
        "qwen/qwen3.7-max":                 {"USD": (0.295, 1.475, 4.425)},
        "moonshotai/kimi-k3":               {"USD": (0.30, 3.00, 15.00)},
        "minimax/minimax-m3":               {"USD": (0.06, 0.30, 1.20)},
        "z-ai/glm-5.2":                     {"USD": (0.14, 0.76, 2.39)},
        "z-ai/glm-5.1":                     {"USD": (0.18, 0.97, 3.04)},
        "xiaomi/mimo-v2.5-pro":             {"USD": (0.0036, 0.435, 0.87)},
        "tencent/hy3":                      {"USD": (0.033, 0.132, 0.528)},
        "stepfun/step-3.7-flash":           {"USD": (0.04, 0.20, 1.15)},
        "nvidia/nemotron-3-super-120b-a12b": {"USD": (None, 0.085, 0.40)},
        "sakana/fugu-ultra":                {"USD": (0.50, 5.00, 30.00)},
        "nvidia/nemotron-3-super-120b-a12b:free": {"USD": (0.0, 0.0, 0.0)},
        "nvidia/nemotron-3-ultra-550b-a55b:free": {"USD": (0.0, 0.0, 0.0)},
        # 暂无价格表（下架/动态价）：openrouter/pareto-code、openrouter/elephant-alpha、
        # poolside/laguna-m.1:free、tencent/hy3:free、inclusionai/ring-2.6-1t:free
    },
    "novita": {
        "minimax/minimax-m2.7":    {"USD": (0.06, 0.30, 1.20)},
        "deepseek/deepseek-v3-0324": {"USD": (0.135, 0.27, 1.12)},
        "deepseek/deepseek-r1-0528": {"USD": (0.35, 0.70, 2.50)},
        "qwen/qwen3-235b-a22b-fp8": {"USD": (None, 0.20, 0.80)},
        "moonshotai/kimi-k2.5":     {"USD": (0.10, 0.60, 3.00)},   # 同源
        "zai-org/glm-5":            {"USD": (0.20, 1.00, 3.20)},   # 同源
    },
    "arcee": {
        "trinity-large-thinking": {"USD": (None, 0.25, 0.80)},
        # 暂无价格表（未托管）：trinity-large-preview / trinity-mini
    },
    "nvidia": {
        # nemotron-super 复用 OR 价；转售按源厂商价（D2）
        "moonshotai/kimi-k2.6": {"USD": (0.16, 0.95, 4.00)},
        "minimaxai/minimax-m3": {"USD": (0.06, 0.30, 1.20)},
        # 暂无价格表：nvidia/nemotron-3-ultra-550b-a55b、nvidia/nemotron-3-nano-omni-30b-a3b-reasoning
        # z-ai/glm-5.2 同键 → OR 价（openrouter 组）
    },
    "alias": {
        # opencode-zen / copilot 独有键，同源取官方价（含别名映射目标键）
        "gpt-5.2":            {"USD": (0.175, 1.75, 14.00)},
        "gpt-5.1":            {"USD": (0.125, 1.25, 10.00)},
        "gpt-5":              {"USD": (0.125, 1.25, 10.00)},
        "minimax-m3":         {"USD": (0.06, 0.30, 1.20)},
        "minimax-m2.7":       {"USD": (0.06, 0.30, 1.20)},
        "minimax-m2.5":       {"USD": (0.03, 0.30, 1.20)},
        "deepseek-v4-flash-free": {"CNY": (0.0, 0.0, 0.0), "USD": (0.0, 0.0, 0.0)},
        "mimo-v2.5-free":     {"USD": (0.0, 0.0, 0.0)},
        "north-mini-code-free": {"USD": (0.0, 0.0, 0.0)},
        "nemotron-3-ultra-free": {"USD": (0.0, 0.0, 0.0)},
        # 暂无价格表：gpt-5.3-codex(-spark) / gpt-5.2-codex / gpt-5.1-codex(-max/-mini) /
        # gpt-5-codex / gpt-5-nano / claude-opus-4-1 / gemini-3.5-flash / gemini-3.1-pro /
        # gemini-3-flash / big-pickle / grok 系列已在 xai 组 / copilot 无官方价的查无项
    },
}

# ── 档位表（输入长度分档；按回合输入量选档，D4） ──────────────────
# model → currency → 档位元组（升序；input_limit=None = 最高档兜底）
@dataclass(frozen=True, slots=True)
class _Tier:
    input_limit: int | None
    output_limit: int | None   # None = 不限（glm-4.7 双维分档用）
    hit: float | None
    miss: float | None
    out: float | None


_TIERS: dict[str, dict[str, tuple[_Tier, ...]]] = {
    "glm-5.1": {"CNY": (_Tier(32_000, None, 1.30, 6.00, 24.00),
                        _Tier(None, None, 2.00, 8.00, 28.00))},
    "glm-5": {"CNY": (_Tier(32_000, None, 1.00, 4.00, 18.00),
                      _Tier(None, None, 1.50, 6.00, 22.00))},
    "glm-5-turbo": {"CNY": (_Tier(32_000, None, 1.20, 5.00, 22.00),
                            _Tier(None, None, 1.80, 7.00, 26.00))},
    "glm-5v-turbo": {"CNY": (_Tier(32_000, None, 1.20, 5.00, 22.00),
                             _Tier(None, None, 1.80, 7.00, 26.00))},
    "glm-4.7": {"CNY": (_Tier(32_000, 199, 0.40, 2.00, 8.00),     # 输入<32K 且输出<200
                        _Tier(32_000, None, 0.60, 3.00, 14.00),   # 输入<32K 且输出≥200
                        _Tier(200_000, None, 0.80, 4.00, 16.00))},  # 输入 32–200K
    "hy3-preview": {"CNY": (_Tier(16_000, None, 0.40, 1.20, 4.00),
                            _Tier(32_000, None, 0.60, 1.60, 6.40),
                            _Tier(None, None, 0.80, 2.00, 8.00))},
    "grok-build-0.1": {"USD": (_Tier(200_000, None, 0.20, 1.00, 2.00),
                               _Tier(None, None, 0.40, 2.00, 4.00))},
    "grok-4.6": {"USD": (_Tier(200_000, None, 0.50, 2.00, 6.00),
                         _Tier(None, None, 1.00, 4.00, 12.00))},
    "grok-4.5": {"USD": (_Tier(200_000, None, 0.30, 2.00, 6.00),
                         _Tier(None, None, 0.60, 4.00, 12.00))},
    "grok-4.3": {"USD": (_Tier(200_000, None, 0.20, 1.25, 2.50),
                         _Tier(None, None, 0.40, 2.50, 5.00))},
    "gemini-3.1-pro-preview": {"USD": (_Tier(200_000, None, 0.20, 2.00, 12.00),
                                       _Tier(None, None, 0.40, 4.00, 18.00))},
}

# ── 峰谷档（DeepSeek 2026-08-17 生效；高峰时段价，空闲=高峰一半） ──
# 官方口径：高峰 = 北京 9:00-12:00 / 14:00-18:00（UTC 01:00-04:00 / 06:00-10:00）。
_PEAK_RATES: dict[str, dict[str, tuple[float, float, float]]] = {
    "deepseek-v4-flash": {"CNY": (0.10, 3.00, 9.00), "USD": (0.014, 0.44, 1.32)},
    "deepseek-v4-pro":   {"CNY": (0.30, 9.00, 27.00), "USD": (0.044, 1.32, 3.96)},
}


def _is_peak_period(dt: datetime | None = None) -> bool:
    """DeepSeek 高峰时段判定（UTC 01:00-04:00 / 06:00-10:00）。"""
    now = dt if dt is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hour = now.astimezone(timezone.utc).hour
    return 1 <= hour < 4 or 6 <= hour < 10

# ── 别名映射（目录 ID → 官方价键，opencode-zen/copilot 同源取价） ──
_ALIAS: dict[str, str] = {
    "claude-opus-4-5":   "claude-opus-4-5-20251101",
    "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
    "claude-sonnet-4":   "claude-sonnet-4-20250514",
    "claude-haiku-4-5":  "claude-haiku-4-5-20251001",
    "claude-sonnet-4.6": "claude-sonnet-4-6",      # copilot 点号写法
    "claude-sonnet-4.5": "claude-sonnet-4-5-20250929",
    "claude-haiku-4.5":  "claude-haiku-4-5-20251001",
}


def _build_pricing() -> dict[str, dict[str, ModelPricing]]:
    result: dict[str, dict[str, ModelPricing]] = {}
    for provider, models in _RAW.items():
        src = _SOURCES[provider]
        for model, currencies in models.items():
            entries: dict[str, ModelPricing] = {}
            for currency, (hit, miss, out_rate) in currencies.items():
                entries[currency] = ModelPricing(
                    miss, out_rate, src, currency=currency,
                    cache_hit_input_cost_per_1M=hit,
                )
            # 同键跨组（minimax USD + minimax-cn CNY）按币种合并，不覆盖
            result.setdefault(model, {}).update(entries)
    return result


_PRICING = _build_pricing()

MODEL_PRICING: Final[Mapping[str, Mapping[str, ModelPricing]]] = MappingProxyType(
    {model: MappingProxyType(entries) for model, entries in _PRICING.items()}
)


def get_model_pricing(
    model: str, currency: str = "CNY", now: datetime | None = None,
) -> ModelPricing | None:
    """Return an exact model entry for one currency; unknown *entry* and absent model differ.

    Falls back through the alias table (目录短名 → 官方价键) before giving up.
    Models in ``_PEAK_RATES`` return the peak-hour rates when ``now`` falls in a
    peak window (defaults to the current UTC time).
    """
    if not isinstance(model, str):
        return None
    key = model.strip()
    entries = MODEL_PRICING.get(key)
    if not entries:
        alias = _ALIAS.get(key)
        if alias:
            entries = MODEL_PRICING.get(alias)
    if not entries:
        return None
    pricing = entries.get(currency.upper())
    if pricing is None:
        return None
    peak = _PEAK_RATES.get(key)
    if peak and _is_peak_period(now):
        rates = peak.get(currency.upper())
        if rates:
            hit, miss, out_rate = rates
            pricing = ModelPricing(
                miss, out_rate, pricing.source + "（高峰档）",
                currency=pricing.currency, cache_hit_input_cost_per_1M=hit,
            )
    return pricing


def estimate_cost(
    model: str,
    *,
    cache_hit_input: int = 0,
    cache_miss_input: int = 0,
    output: int = 0,
    currency: str = "CNY",
    now: datetime | None = None,
) -> float | None:
    """Estimate one call's cost, or ``None`` when exact pricing is unavailable.

    Tiered models are priced by per-turn input total (hit + miss) against
    ``_TIERS``; the first tier whose limits fit wins, otherwise the last tier.
    Models without a cache tier (``cache_hit_input_cost_per_1M is None``) are
    billed at the standard input rate for every input token.
    ``now`` pins the peak/off-peak window for DeepSeek (defaults to now).
    """
    pricing = get_model_pricing(model, currency, now=now)
    if pricing is None or not pricing.known:
        return None
    try:
        hit_count = int(cache_hit_input)
        miss_count = int(cache_miss_input)
        output_count = int(output)
    except (TypeError, ValueError, OverflowError):
        return None
    if hit_count < 0 or miss_count < 0 or output_count < 0:
        return None

    tiers = _TIERS.get(model, {}).get(currency.upper())
    if tiers:
        input_total = hit_count + miss_count
        tier = next(
            (t for t in tiers
             if (t.input_limit is None or input_total <= t.input_limit)
             and (t.output_limit is None or output_count <= t.output_limit)),
            tiers[-1],
        )
        hit_rate, miss_rate, out_rate = tier.hit, tier.miss, tier.out
        if hit_rate is None:
            hit_rate = miss_rate
        if hit_rate is None or miss_rate is None or out_rate is None:
            return None
    else:
        hit_rate = pricing.cache_hit_input_cost_per_1M
        if hit_rate is None:
            hit_rate = pricing.input_cost_per_1M
        miss_rate = pricing.input_cost_per_1M
        out_rate = pricing.output_cost_per_1M
    return (
        (hit_count / 1_000_000) * float(hit_rate)
        + (miss_count / 1_000_000) * float(miss_rate)
        + (output_count / 1_000_000) * float(out_rate)
    )


def estimate_cost_usd(
    model: str, input_tokens: int, output_tokens: int,
) -> float | None:
    """Legacy two-bucket estimate; kept for callers that only track input/output."""
    return estimate_cost(
        model,
        cache_hit_input=0,
        cache_miss_input=input_tokens,
        output=output_tokens,
        currency="USD",
    )


def pricing_payload(
    model: str, currency: str = "CNY", now: datetime | None = None,
) -> dict[str, object]:
    """JSON-safe metadata for the frontend's model/pricing section."""
    pricing = get_model_pricing(model, currency, now=now)
    if pricing is None:
        return {
            "model": model,
            "known": False,
            "input_cost_per_1M": None,
            "output_cost_per_1M": None,
            "cache_hit_input_cost_per_1M": None,
            "currency": currency,
            "source": "unknown",
            "rate_period": None,
        }
    return {
        "model": model,
        "known": pricing.known,
        "input_cost_per_1M": pricing.input_cost_per_1M,
        "output_cost_per_1M": pricing.output_cost_per_1M,
        "cache_hit_input_cost_per_1M": pricing.cache_hit_input_cost_per_1M,
        "currency": pricing.currency,
        "source": pricing.source,
        "rate_period": "peak" if _is_peak_period(now) else "off_peak",
    }
