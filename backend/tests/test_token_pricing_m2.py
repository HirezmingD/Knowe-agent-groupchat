"""M2 价格表落库单测：分档选档 / 别名映射 / 免费 / 无缓存档 / 区间下沿 / 暂无价格表。"""

from __future__ import annotations

from backend.token_pricing import estimate_cost, get_model_pricing


def _cost(model: str, *, hit: int = 0, miss: int = 0, out: int = 0, currency: str = "USD"):
    return estimate_cost(model, cache_hit_input=hit, cache_miss_input=miss,
                         output=out, currency=currency)


# ── 分档选档（_TIERS） ────────────────────────────────────────────

def test_tier_glm_5_1_cny_boundary():
    # <32K 用低档（1.30/6.00/24.00）；≥32K 用高档（2.00/8.00/28.00）
    low = _cost("glm-5.1", hit=10_000, miss=10_000, out=10_000, currency="CNY")
    assert abs(low - (0.013 + 0.06 + 0.24)) < 1e-9
    high = _cost("glm-5.1", hit=20_000, miss=20_000, out=10_000, currency="CNY")
    assert abs(high - (0.04 + 0.16 + 0.28)) < 1e-9
    # 边界恰等 32K → 低档（≤）
    edge = _cost("glm-5.1", hit=32_000, miss=0, out=0, currency="CNY")
    assert abs(edge - 32_000 / 1e6 * 1.30) < 1e-9


def test_tier_hy3_preview_three_levels():
    assert abs(_cost("hy3-preview", miss=16_000, out=0, currency="CNY")
               - 16_000 / 1e6 * 1.20) < 1e-9          # ≤16K 档
    assert abs(_cost("hy3-preview", miss=30_000, out=0, currency="CNY")
               - 30_000 / 1e6 * 1.60) < 1e-9          # 16–32K 档
    assert abs(_cost("hy3-preview", miss=40_000, out=0, currency="CNY")
               - 40_000 / 1e6 * 2.00) < 1e-9          # ≥32K 档


def test_tier_glm_4_7_dual_dimension():
    # 输入<32K 输出<200 → 档1；输入<32K 输出≥200 → 档2；输入 32–200K → 档3
    t1 = _cost("glm-4.7", miss=10_000, out=100, currency="CNY")
    assert abs(t1 - (10_000 / 1e6 * 2.00 + 100 / 1e6 * 8.00)) < 1e-9
    t2 = _cost("glm-4.7", miss=10_000, out=500, currency="CNY")
    assert abs(t2 - (10_000 / 1e6 * 3.00 + 500 / 1e6 * 14.00)) < 1e-9
    t3 = _cost("glm-4.7", miss=50_000, out=1_000, currency="CNY")
    assert abs(t3 - (50_000 / 1e6 * 4.00 + 1_000 / 1e6 * 16.00)) < 1e-9


def test_tier_grok_4_5_context_window():
    # 短上下文 0.30/2.00/6.00；长上下文（≥200K）0.60/4.00/12.00
    short = _cost("grok-4.5", miss=100_000, out=50_000)
    assert abs(short - (0.20 + 0.30)) < 1e-9
    long_ = _cost("grok-4.5", miss=300_000, out=50_000)
    assert abs(long_ - (1.20 + 0.60)) < 1e-9


def test_tier_gemini_pro_200k():
    assert abs(_cost("gemini-3.1-pro-preview", miss=200_000, out=0)
               - 0.40) < 1e-9          # ≤200K 档（2.00/M）
    assert abs(_cost("gemini-3.1-pro-preview", miss=250_000, out=0)
               - 1.00) < 1e-9          # >200K 档（4.00/M）


# ── 别名映射（同源取价） ──────────────────────────────────────────

def test_alias_claude_opus_4_5():
    # opencode-zen 短名 → 官方 claude-opus-4-5-20251101 价（0.50/5.00/25.00）
    pricing = get_model_pricing("claude-opus-4-5", "USD")
    assert pricing is not None and pricing.known
    assert pricing.input_cost_per_1M == 5.00
    assert pricing.cache_hit_input_cost_per_1M == 0.50


def test_alias_copilot_dot_notation():
    # copilot 的 claude-sonnet-4.6（点号）→ 官方 claude-sonnet-4-6
    pricing = get_model_pricing("claude-sonnet-4.6", "USD")
    assert pricing is not None and pricing.known
    assert pricing.input_cost_per_1M == 3.00


def test_alias_miss_returns_none():
    # 映射目标也不存在的 → None（暂无价格表）
    assert get_model_pricing("no-such-model", "USD") is None
    assert get_model_pricing("claude-opus-4-1", "USD") is None  # 官方无此型号


# ── 特殊口径 ─────────────────────────────────────────────────────

def test_free_model_zero_cost():
    assert _cost("glm-4.5-flash", hit=1_000_000, miss=1_000_000, out=1_000_000) == 0.0
    assert _cost("deepseek-v4-flash-free", miss=1_000, out=1_000, currency="CNY") == 0.0


def test_interval_lower_bound_qwen():
    # D5：USD 区间取下沿（qwen3.7-plus $0.32–0.96 / $1.28–3.84）
    assert _cost("qwen3.7-plus", miss=1_000_000, out=1_000_000) == 0.32 + 1.28


def test_unpriced_returns_none():
    # 官方未出价 / GMI 独有键 / 下架模型
    assert _cost("gpt-5.6-sol-pro", miss=1_000) is None
    assert _cost("zai-org/GLM-5.1-FP8", miss=1_000) is None
    assert _cost("openrouter/pareto-code", miss=1_000) is None
    assert _cost("moonshotai/Kimi-K2.5", miss=1_000) is None  # HF 大写键


def test_reseller_same_key_uses_official_rate():
    # nvidia 转售 minimaxai/minimax-m3 = MiniMax 官方价（D2）
    pricing = get_model_pricing("minimaxai/minimax-m3", "USD")
    assert pricing is not None
    assert pricing.input_cost_per_1M == 0.30
    # z-ai/glm-5.2 同键冲突 → OR 价（平台 API 拉取最权威）
    pricing = get_model_pricing("z-ai/glm-5.2", "USD")
    assert pricing is not None
    assert pricing.input_cost_per_1M == 0.76
    assert pricing.cache_hit_input_cost_per_1M == 0.14


def test_spot_check_per_provider():
    """每供应商抽查 1 个模型，防整组数据漂移。"""
    cases = {
        "gpt-5.6-sol": (0.50, 5.00, 30.00),
        "claude-fable-5": (1.00, 10.00, 50.00),
        "gemini-3.6-flash": (0.15, 1.50, 7.50),
        "glm-5.2": (0.26, 1.40, 4.40),
        "kimi-k3": (0.30, 3.00, 15.00),
        "deepseek-v4-pro": (0.003625, 0.435, 0.87),
        "qwen3.7-max": (None, 1.25, 3.75),  # USD 无缓存档（1.20 是 CNY 百炼命中价）
        "qwen/qwen3.8-max": (0.40, 2.00, 6.00),  # [v1.0.24.2] OR 实时价（缓存=输入×0.2）
        "MiniMax-M3": (0.06, 0.30, 1.20),
        "grok-4.20-0309-reasoning": (0.20, 1.25, 2.50),
        "anthropic/claude-opus-5": (0.50, 5.00, 25.00),
        "openai/gpt-5.6-luna": (0.01, 0.10, 0.60),
        "deepseek/deepseek-v3-0324": (0.135, 0.27, 1.12),
        "trinity-large-thinking": (None, 0.25, 0.80),
        "nvidia/nemotron-3-super-120b-a12b": (None, 0.085, 0.40),
        "gpt-5.4-pro": (None, 30.00, 180.00),
        "claude-opus-4-5-20251101": (0.50, 5.00, 25.00),
        "tencent/hy3": (0.033, 0.132, 0.528),
    }
    for model, (hit, miss, out) in cases.items():
        pricing = get_model_pricing(model, "USD")
        assert pricing is not None, f"{model} 应有 USD 价"
        assert pricing.cache_hit_input_cost_per_1M == hit, model
        assert pricing.input_cost_per_1M == miss, model
        assert pricing.output_cost_per_1M == out, model
    # 纯 CNY 模型（阶跃官方无 USD 价）
    pricing = get_model_pricing("step-3.5-flash", "CNY")
    assert pricing is not None and pricing.known
    assert pricing.cache_hit_input_cost_per_1M == 0.14
    assert pricing.input_cost_per_1M == 0.70
    assert pricing.output_cost_per_1M == 2.10
    assert get_model_pricing("step-3.5-flash", "USD") is None
    # [v1.0.24.2] qwen3.8-max 纯 CNY 档（官方页仅人民币价；USD 未公布）
    pricing = get_model_pricing("qwen3.8-max", "CNY")
    assert pricing is not None and pricing.known
    assert pricing.cache_hit_input_cost_per_1M == 1.50
    assert pricing.input_cost_per_1M == 12.00
    assert pricing.output_cost_per_1M == 36.00
    # USD 键存在但未公布 → 返回 known=False 对象（input_cost 为 None），不报「暂无价格表」歧义
    pricing = get_model_pricing("qwen3.8-max", "USD")
    assert pricing is not None and pricing.input_cost_per_1M is None
