/**
 * modelCatalog.ts — 大模型厂商目录（[v0.44] 设置 · 模型与提供方）
 *
 * 摆放位置：src/store/modelCatalog.ts
 *
 * ★ 目录**不是编造的**：逐条抽取自上游模型目录源码（README §四 约束 2）——
 *   · 厂商清单与显示名 ← models.py 的 CANONICAL_PROVIDERS（slug + label 原文照搬）
 *   · 各厂商模型列表   ← models.py 的 _PROVIDER_MODELS / OPENROUTER_MODELS（id 原文照搬）
 *   · 传输协议         ← providers.py 的传输协议覆盖表
 *   · 接入点 base URL  ← providers.py 的 base_url_override，或 models.py 里的硬编码端点
 *     （openrouter.ai/api/v1、api.novita.ai/openai/v1、api.anthropic.com、
 *      api.githubcopilot.com、dashscope-intl compatible-mode）；两处都没有的少数厂商
 *     用其官方文档公开端点补齐（逐条注明）。
 *
 * [v1.0.19.5] 全量同步上游 models.py 快照：新增 GPT-5.6 系列、Claude Opus 5 /
 *   Sonnet 5 / Fable 5、Kimi K3、Gemini 3.6 Flash、Grok 4.5、Sakana Fugu Ultra、
 *   Tencent hy3（hy3-preview 转正）；同步移除上游已删模型（deepseek-chat /
 *   deepseek-reasoner / hy3-preview / gemini-3.5-flash / grok-4.3 等）。
 *   已保存配置里的旧模型名由 MODEL_MIGRATIONS 自动迁移（见 migrateModelName）。
 *
 * ★ 有意**不收**的服务商（收了也用不了，反而骗人）：
 *   · OAuth / 外部进程类（API Key 流程装不下）：nous、openai-codex、xai-oauth、
 *     qwen-oauth、google-gemini-cli、minimax-oauth、copilot-acp、bedrock(aws_sdk)、
 *     vertex、qwen-oauth
 *   · 无静态模型目录（第二步下拉会是空的）：azure-foundry、lmstudio、ollama-cloud、
 *     local/custom、fireworks（动态目录）
 *   · 接入点在上游源里查不到、又不敢瞎写的：xiaomi（仅 env 覆盖，无公开默认端点）
 *
 * cheap 字段 = 该厂商目录里的便宜档（辅助模型「不设置则用主模型服务商便宜档」的落点），
 * 从各厂商自己的模型列表里选 flash/mini/nano 档，不跨厂商。
 */

export type Transport = 'openai_chat' | 'anthropic_messages' | 'codex_responses';

export interface ProviderEntry {
  /** 规范 slug（CANONICAL_PROVIDERS 原文） */
  slug: string;
  /** 显示名（CANONICAL_PROVIDERS.label 原文） */
  label: string;
  transport: Transport;
  /** 接入点。连接测试与后端调用都用它。 */
  baseUrl: string;
  /** 该厂商目录里的便宜档模型（辅助模型缺省档） */
  cheap: string;
  /** 模型 id 列表（上游源码原文顺序） */
  models: string[];
}

export const PROVIDERS: ProviderEntry[] = [
  {
    slug: 'openrouter', label: 'OpenRouter', transport: 'openai_chat',
    baseUrl: 'https://openrouter.ai/api/v1',
    cheap: 'deepseek/deepseek-v4-flash',
    models: [
      'anthropic/claude-fable-5',
      'anthropic/claude-opus-5',
      'anthropic/claude-opus-5-fast',
      'anthropic/claude-opus-4.8',
      'anthropic/claude-opus-4.8-fast',
      'anthropic/claude-sonnet-5',
      'anthropic/claude-haiku-4.5',
      'openai/gpt-5.6-sol',
      'openai/gpt-5.6-sol-pro',
      'openai/gpt-5.6-terra',
      'openai/gpt-5.6-terra-pro',
      'openai/gpt-5.6-luna',
      'openai/gpt-5.6-luna-pro',
      'openai/gpt-5.5',
      'openai/gpt-5.5-pro',
      'openai/gpt-5.4-mini',
      'google/gemini-3.1-pro-preview',
      'google/gemini-3.6-flash',
      'x-ai/grok-4.6',
      'x-ai/grok-4.5',
      'deepseek/deepseek-v4-pro',
      'deepseek/deepseek-v4-flash',
      'qwen/qwen3.8-max',
      'moonshotai/kimi-k3',
      'minimax/minimax-m3',
      'z-ai/glm-5.2',
      'z-ai/glm-5.1',
      'xiaomi/mimo-v2.5-pro',
      'tencent/hy3',
      'stepfun/step-3.7-flash',
      'nvidia/nemotron-3-super-120b-a12b',
      'sakana/fugu-ultra',
      'openrouter/pareto-code',
      'openrouter/elephant-alpha',
      'poolside/laguna-m.1:free',
      'tencent/hy3:free',
      'nvidia/nemotron-3-super-120b-a12b:free',
      'nvidia/nemotron-3-ultra-550b-a55b:free',
      'inclusionai/ring-2.6-1t:free',
    ],
  },
  {
    slug: 'deepseek', label: 'DeepSeek', transport: 'openai_chat',
    baseUrl: 'https://api.deepseek.com',
    cheap: 'deepseek-v4-flash',
    models: [
      'deepseek-v4-pro',
      'deepseek-v4-flash',
    ],
  },
  {
    slug: 'zai', label: 'Z.AI / GLM', transport: 'openai_chat',
    baseUrl: 'https://api.z.ai/api/paas/v4',
    cheap: 'glm-4.5-flash',
    models: [
      'glm-5.2',
      'glm-5.1',
      'glm-5',
      'glm-5v-turbo',
      'glm-5-turbo',
      'glm-4.7',
      'glm-4.5',
      'glm-4.5-flash',
    ],
  },
  {
    slug: 'kimi-coding', label: 'Kimi / Kimi Coding Plan', transport: 'openai_chat',
    baseUrl: 'https://api.moonshot.ai/v1',
    cheap: 'kimi-k2.5',
    models: [
      'kimi-k3',
      'kimi-k2.7-code',
      'kimi-k2.6',
      'kimi-k2.5',
      'kimi-for-coding',
      'kimi-for-coding-highspeed',
      'kimi-k2-thinking',
      'kimi-k2-thinking-turbo',
      'kimi-k2-turbo-preview',
      'kimi-k2-0905-preview',
    ],
  },
  {
    slug: 'kimi-coding-cn', label: 'Kimi / Moonshot (China)', transport: 'openai_chat',
    baseUrl: 'https://api.moonshot.cn/v1',
    cheap: 'kimi-k2.5',
    models: [
      'kimi-k3',
      'kimi-k2.7-code',
      'kimi-k2.7-code-highspeed',
      'kimi-k2.6',
      'kimi-k2.5',
      'kimi-k2-thinking',
      'kimi-k2-turbo-preview',
      'kimi-k2-0905-preview',
    ],
  },
  {
    slug: 'alibaba', label: 'Qwen Cloud', transport: 'openai_chat',
    baseUrl: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
    cheap: 'qwen3.5-plus',
    models: [
      'qwen3.8-max',
      'qwen3.7-max',
      'qwen3.7-plus',
      'qwen3.6-plus',
      'kimi-k2.5',
      'qwen3.5-plus',
      'qwen3-coder-plus',
      'qwen3-coder-next',
      'glm-5',
      'glm-4.7',
      'MiniMax-M2.5',
    ],
  },
  {
    slug: 'minimax', label: 'MiniMax', transport: 'anthropic_messages',
    baseUrl: 'https://api.minimax.io/anthropic',
    cheap: 'MiniMax-M2',
    models: [
      'MiniMax-M3',
      'MiniMax-M2.7',
      'MiniMax-M2.5',
      'MiniMax-M2.1',
      'MiniMax-M2',
    ],
  },
  {
    slug: 'minimax-cn', label: 'MiniMax (China)', transport: 'anthropic_messages',
    baseUrl: 'https://api.minimaxi.com/anthropic',
    cheap: 'MiniMax-M2',
    models: [
      'MiniMax-M3',
      'MiniMax-M2.7',
      'MiniMax-M2.5',
      'MiniMax-M2.1',
      'MiniMax-M2',
    ],
  },
  {
    slug: 'stepfun', label: 'StepFun Step Plan', transport: 'openai_chat',
    baseUrl: 'https://api.stepfun.ai/step_plan/v1',
    cheap: 'step-3.5-flash',
    models: [
      'step-3.5-flash',
      'step-3.5-flash-2603',
    ],
  },
  {
    slug: 'tencent-tokenhub', label: 'Tencent TokenHub', transport: 'openai_chat',
    baseUrl: 'https://tokenhub.tencentmaas.com/v1',
    cheap: 'hy3-preview',
    models: [
      'hy3-preview',
    ],
  },
  {
    slug: 'xai', label: 'xAI', transport: 'codex_responses',
    baseUrl: 'https://api.x.ai/v1',
    cheap: 'grok-4.20-0309-non-reasoning',
    models: [
      'grok-build-0.1',
      'grok-4.6',
      'grok-4.5',
      'grok-4.3',
      'grok-4.20-0309-reasoning',
      'grok-4.20-0309-non-reasoning',
      'grok-4.20-multi-agent-0309',
    ],
  },
  {
    slug: 'anthropic', label: 'Anthropic', transport: 'anthropic_messages',
    baseUrl: 'https://api.anthropic.com',
    cheap: 'claude-haiku-4-5-20251001',
    models: [
      'claude-fable-5',
      'claude-sonnet-5',
      'claude-opus-4-8',
      'claude-opus-4-7',
      'claude-opus-4-6',
      'claude-sonnet-4-6',
      'claude-opus-4-5-20251101',
      'claude-sonnet-4-5-20250929',
      'claude-opus-4-20250514',
      'claude-sonnet-4-20250514',
      'claude-haiku-4-5-20251001',
    ],
  },
  {
    slug: 'openai-api', label: 'OpenAI API', transport: 'codex_responses',
    baseUrl: 'https://api.openai.com/v1',
    cheap: 'gpt-5.4-nano',
    models: [
      'gpt-5.6-sol',
      'gpt-5.6-sol-pro',
      'gpt-5.6-terra',
      'gpt-5.6-terra-pro',
      'gpt-5.6-luna',
      'gpt-5.6-luna-pro',
      'gpt-5.5',
      'gpt-5.5-pro',
      'gpt-5.4',
      'gpt-5.4-mini',
      'gpt-5.4-nano',
      'gpt-5-mini',
      'gpt-5.3-codex',
      'gpt-4.1',
      'gpt-4o',
      'gpt-4o-mini',
    ],
  },
  {
    slug: 'gemini', label: 'Google AI Studio', transport: 'openai_chat',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    cheap: 'gemini-3.6-flash',
    models: [
      'gemini-3.1-pro-preview',
      'gemini-3-pro-preview',
      'gemini-3.6-flash',
      'gemini-3.1-flash-lite-preview',
    ],
  },
  {
    slug: 'nvidia', label: 'NVIDIA NIM', transport: 'openai_chat',
    baseUrl: 'https://integrate.api.nvidia.com/v1',
    cheap: 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning',
    models: [
      'nvidia/nemotron-3-ultra-550b-a55b',
      'nvidia/nemotron-3-super-120b-a12b',
      'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning',
      'z-ai/glm-5.2',
      'moonshotai/kimi-k2.6',
      'minimaxai/minimax-m3',
    ],
  },
  {
    slug: 'huggingface', label: 'Hugging Face', transport: 'openai_chat',
    baseUrl: 'https://router.huggingface.co/v1',
    cheap: 'XiaomiMiMo/MiMo-V2-Flash',
    models: [
      'moonshotai/Kimi-K2.5',
      'Qwen/Qwen3.5-397B-A17B',
      'Qwen/Qwen3.5-35B-A3B',
      'deepseek-ai/DeepSeek-V3.2',
      'MiniMaxAI/MiniMax-M2.5',
      'zai-org/GLM-5',
      'XiaomiMiMo/MiMo-V2-Flash',
      'moonshotai/Kimi-K2-Thinking',
      'moonshotai/Kimi-K2.6',
    ],
  },
  {
    slug: 'novita', label: 'NovitaAI', transport: 'openai_chat',
    baseUrl: 'https://api.novita.ai/openai/v1',
    cheap: 'deepseek/deepseek-v3-0324',
    models: [
      'moonshotai/kimi-k2.5',
      'minimax/minimax-m2.7',
      'zai-org/glm-5',
      'deepseek/deepseek-v3-0324',
      'deepseek/deepseek-r1-0528',
      'qwen/qwen3-235b-a22b-fp8',
    ],
  },
  {
    slug: 'arcee', label: 'Arcee AI', transport: 'openai_chat',
    baseUrl: 'https://api.arcee.ai/api/v1',
    cheap: 'trinity-mini',
    models: [
      'trinity-large-thinking',
      'trinity-large-preview',
      'trinity-mini',
    ],
  },
  {
    slug: 'gmi', label: 'GMI Cloud', transport: 'openai_chat',
    baseUrl: 'https://api.gmi-serving.com/v1',
    cheap: 'google/gemini-3.1-flash-lite-preview',
    models: [
      'zai-org/GLM-5.1-FP8',
      'deepseek-ai/DeepSeek-V3.2',
      'moonshotai/Kimi-K2.5',
      'google/gemini-3.1-flash-lite-preview',
      'anthropic/claude-sonnet-5',
      'anthropic/claude-sonnet-4.6',
      'openai/gpt-5.4',
    ],
  },
  {
    slug: 'copilot', label: 'GitHub Copilot', transport: 'openai_chat',
    baseUrl: 'https://api.githubcopilot.com',
    cheap: 'gpt-4o-mini',
    models: [
      'gpt-5.4',
      'gpt-5.4-mini',
      'gpt-5-mini',
      'gpt-5.3-codex',
      'gpt-5.2-codex',
      'gpt-4.1',
      'gpt-4o',
      'gpt-4o-mini',
      'claude-sonnet-4.6',
      'claude-sonnet-5',
      'claude-sonnet-4',
      'claude-sonnet-4.5',
      'claude-haiku-4.5',
      'gemini-3.1-pro-preview',
      'gemini-3-pro-preview',
      'gemini-3-flash-preview',
      'gemini-2.5-pro',
    ],
  },
  {
    slug: 'opencode-zen', label: 'OpenCode Zen', transport: 'openai_chat',
    baseUrl: 'https://opencode.ai/zen/v1',
    cheap: 'gpt-5-nano',
    models: [
      'kimi-k2.5',
      'kimi-k2.6',
      'gpt-5.5',
      'gpt-5.5-pro',
      'gpt-5.4-pro',
      'gpt-5.4',
      'gpt-5.4-mini',
      'gpt-5.4-nano',
      'gpt-5.3-codex',
      'gpt-5.3-codex-spark',
      'gpt-5.2',
      'gpt-5.2-codex',
      'gpt-5.1',
      'gpt-5.1-codex',
      'gpt-5.1-codex-max',
      'gpt-5.1-codex-mini',
      'gpt-5',
      'gpt-5-codex',
      'gpt-5-nano',
      'claude-fable-5',
      'claude-sonnet-5',
      'claude-opus-4-8',
      'claude-opus-4-7',
      'claude-opus-4-6',
      'claude-opus-4-5',
      'claude-opus-4-1',
      'claude-sonnet-4-6',
      'claude-sonnet-4-5',
      'claude-sonnet-4',
      'claude-haiku-4-5',
      'gemini-3.5-flash',
      'gemini-3.1-pro',
      'gemini-3-flash',
      'minimax-m3',
      'minimax-m2.7',
      'minimax-m2.5',
      'glm-5.2',
      'glm-5.1',
      'glm-5',
      'kimi-k2.7-code',
      'deepseek-v4-pro',
      'deepseek-v4-flash',
      'deepseek-v4-flash-free',
      'qwen3.7-plus',
      'qwen3.6-plus',
      'qwen3.5-plus',
      'grok-build-0.1',
      'big-pickle',
      'mimo-v2.5-free',
      'north-mini-code-free',
      'nemotron-3-ultra-free',
    ],
  },
];

/**
 * [v1.0.19.5] 旧模型名 → 新模型名（按 provider 维度）。
 * 上游 models.py 移除/改名的模型，已保存配置里的旧名在目录里查不到——
 * 保存/对账时按此表自动迁移到当前名。只列**明确同档替代**；无替代的
 * （如 openrouter/owl-alpha）保留原名，由用户在 UI 里重选。
 */
export const MODEL_MIGRATIONS: Record<string, Record<string, string>> = {
  'openrouter': {
    'anthropic/claude-sonnet-4.6': 'anthropic/claude-sonnet-5',
    'google/gemini-3-pro-preview': 'google/gemini-3.1-pro-preview',
    'google/gemini-3.5-flash': 'google/gemini-3.6-flash',
    'moonshotai/kimi-k2.6': 'moonshotai/kimi-k3',
    'moonshotai/kimi-k2.7-code': 'moonshotai/kimi-k3',
    'tencent/hy3-preview': 'tencent/hy3',
    'tencent/hy3-preview:free': 'tencent/hy3:free',
    'x-ai/grok-4.3': 'x-ai/grok-4.5',
  },
  'deepseek': {
    'deepseek-chat': 'deepseek-v4-flash',
    'deepseek-reasoner': 'deepseek-v4-pro',
  },
  'gemini': {
    'gemini-3.5-flash': 'gemini-3.6-flash',
  },
  'opencode-zen': {
    'claude-3-5-haiku': 'claude-haiku-4-5',
    'gemini-3-pro': 'gemini-3.1-pro',
    'glm-4.6': 'glm-5.1',
    'glm-4.7': 'glm-5.1',
    'kimi-k2': 'kimi-k2.6',
    'gpt-5': 'gpt-5.4',
    'gpt-5.1': 'gpt-5.4',
    'minimax-m2.1': 'minimax-m2.5',
  },
};

/** 旧模型名 → 新模型名；无映射时原样返回。 */
export function migrateModelName(provider: string, model: string): string {
  if (!provider || !model) return model;
  return MODEL_MIGRATIONS[provider]?.[model] ?? model;
}

/** slug → 条目（找不到 → undefined，调用方自兜底） */
export function providerOf(slug: string): ProviderEntry | undefined {
  return PROVIDERS.find((p) => p.slug === slug);
}

export function providerLabel(slug: string): string {
  return providerOf(slug)?.label ?? slug;
}

export function modelsOf(slug: string): string[] {
  return providerOf(slug)?.models ?? [];
}

/** 该厂商的便宜档（辅助模型缺省时的回落档）。 */
export function cheapTierOf(slug: string): string {
  const p = providerOf(slug);
  if (!p) return '';
  return p.cheap || p.models[p.models.length - 1] || '';
}
