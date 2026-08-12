/**
 * ModelBindingModule.tsx — [v0.44] 「厂商 → 模型 → API Key → 确定/修改」四步模块
 *
 * 摆放位置：src/components/ModelBindingModule.tsx
 *
 * 被三处复用（README §2.2 与 §3.2 要求交互逻辑完全一致）：
 *   · 设置 → 模型与提供方 → 主模型模块
 *   · 设置 → 模型与提供方 → 辅助模型模块
 *   · 联系人 → Worker/主管资料页 → Agent 模型独立设置
 *
 * 交互铁律（README §2.2(1) 五步 + §四 约束 3）：
 *   ① 「大模型提供商」下拉：列出 modelCatalog 里全部可选服务商；
 *   ② 「主模型」下拉：未选厂商时**禁用且为空**；选定厂商后才展开该厂商的模型列表；
 *   ③ 「API Key」：type=password 星号遮蔽；onCopy/onCut preventDefault（可粘贴不可复制）；
 *      **厂商或模型一变，Key 自动清空**；
 *   ④ 「确定」：三个输入框变灰、disabled、封存，只留「修改」按钮；
 *   ⑤ 「修改」：三个框恢复可编辑。
 *
 * 数据都在 settings store（父组件传 binding + onSave/onEdit），本组件只管草稿态与交互。
 */

import React, { useEffect, useState } from 'react';
import { PROVIDERS, modelsOf } from '../store/modelCatalog';
import type { ModelBinding, TestResult } from '../store/settings';
import { testBinding } from '../store/settings'; // [v1.0.24.1] 确认后自动测连接
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';

/**
 * 厂商 → 国旗标识（仅 UI 国别标识，帮新人一眼认出服务商归属）。
 * 不放进 modelCatalog.ts：该文件逐条同步自上游目录（原文照搬），避免污染。
 * cn/us = 内联 SVG 国旗（不依赖系统 emoji 字体：Win10 的 Segoe UI Emoji 没有彩色国旗字形，
 *          会把 🇨🇳 渲染成字母 "CN"，故用图片保证任何平台一致）。
 * globe = 🌐 聚合/社区平台：不归单一国别，标国旗会误导新人
 *         （OpenRouter/NovitaAI 聚合全球模型、Hugging Face 全球模型社区、GitHub 全球代码社区）。
 * 若未来上游新增厂商，slug 不在表内 → 自动不显示国旗，安全降级。
 */
type FlagKind = 'cn' | 'us' | 'globe';
const PROVIDER_FLAGS: Record<string, FlagKind> = {
  deepseek: 'cn',
  zai: 'cn',
  'kimi-coding': 'cn',
  'kimi-coding-cn': 'cn',
  alibaba: 'cn',
  minimax: 'cn',
  'minimax-cn': 'cn',
  stepfun: 'cn',
  'tencent-tokenhub': 'cn',
  'opencode-zen': 'cn',
  xai: 'us',
  anthropic: 'us',
  'openai-api': 'us',
  gemini: 'us',
  nvidia: 'us',
  arcee: 'us',
  gmi: 'us',
  openrouter: 'globe',
  novita: 'globe',
  huggingface: 'globe',
  copilot: 'globe',
};

/** 中国厂商的公司/品牌中文名（显示为「中文名 英文名」，如 深度求索 DeepSeek）。 */
const PROVIDER_CN_NAMES: Record<string, string> = {
  deepseek: 'model.binding.module.06',
  zai: 'model.binding.module.03',
  'kimi-coding': 'model.binding.module.04',
  'kimi-coding-cn': 'model.binding.module.04',
  alibaba: 'model.binding.module.14',
  minimax: 'model.binding.module.09',
  'minimax-cn': 'model.binding.module.09',
  stepfun: 'model.binding.module.15',
  'tencent-tokenhub': 'model.binding.module.11',
  'opencode-zen': 'model.binding.module.08',
};

/** 中美国旗 SVG（data URI 图片）。坐标按标准 3:2 旗面生成。 */
const FLAG_DATA_URI: Record<'cn' | 'us', string> = {
  cn: 'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 20">' +
    '<rect width="30" height="20" fill="#DE2910"/>' +
    '<polygon points="5.00,2.00 5.71,4.03 7.85,4.07 6.14,5.37 6.76,7.43 5.00,6.20 3.24,7.43 3.86,5.37 2.15,4.07 4.29,4.03" fill="#FFDE00"/>' +
    '<polygon points="9.14,2.51 9.60,1.96 9.25,1.34 9.91,1.61 10.39,1.08 10.34,1.79 11.00,2.09 10.30,2.26 10.22,2.97 9.84,2.37" fill="#FFDE00"/>' +
    '<polygon points="11.01,4.14 11.65,3.81 11.56,3.10 12.07,3.61 12.72,3.30 12.40,3.94 12.88,4.47 12.18,4.36 11.83,4.99 11.71,4.28" fill="#FFDE00"/>' +
    '<polygon points="11.01,5.86 11.71,5.72 11.83,5.01 12.18,5.64 12.88,5.53 12.40,6.06 12.72,6.70 12.07,6.39 11.56,6.90 11.65,6.19" fill="#FFDE00"/>' +
    '<polygon points="9.14,7.49 9.84,7.63 10.22,7.03 10.30,7.74 11.00,7.91 10.34,8.21 10.39,8.92 9.91,8.39 9.25,8.66 9.60,8.04" fill="#FFDE00"/>' +
    '</svg>'),
  us: 'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 20">' +
    '<rect y="0.000" width="30" height="1.538" fill="#B22234"/><rect y="1.538" width="30" height="1.538" fill="#FFFFFF"/>' +
    '<rect y="3.077" width="30" height="1.538" fill="#B22234"/><rect y="4.615" width="30" height="1.538" fill="#FFFFFF"/>' +
    '<rect y="6.154" width="30" height="1.538" fill="#B22234"/><rect y="7.692" width="30" height="1.538" fill="#FFFFFF"/>' +
    '<rect y="9.231" width="30" height="1.538" fill="#B22234"/><rect y="10.769" width="30" height="1.538" fill="#FFFFFF"/>' +
    '<rect y="12.308" width="30" height="1.538" fill="#B22234"/><rect y="13.846" width="30" height="1.538" fill="#FFFFFF"/>' +
    '<rect y="15.385" width="30" height="1.538" fill="#B22234"/><rect y="16.923" width="30" height="1.538" fill="#FFFFFF"/>' +
    '<rect y="18.462" width="30" height="1.538" fill="#B22234"/>' +
    '<rect width="12" height="10.769" fill="#3C3B6E"/>' +
    '<polygon points="3.00,1.59 3.26,2.34 4.05,2.35 3.42,2.83 3.65,3.58 3.00,3.13 2.35,3.58 2.58,2.83 1.95,2.35 2.74,2.34" fill="#FFFFFF"/>' +
    '<polygon points="6.00,1.59 6.26,2.34 7.05,2.35 6.42,2.83 6.65,3.58 6.00,3.13 5.35,3.58 5.58,2.83 4.95,2.35 5.74,2.34" fill="#FFFFFF"/>' +
    '<polygon points="9.00,1.59 9.26,2.34 10.05,2.35 9.42,2.83 9.65,3.58 9.00,3.13 8.35,3.58 8.58,2.83 7.95,2.35 8.74,2.34" fill="#FFFFFF"/>' +
    '<polygon points="3.00,4.28 3.26,5.03 4.05,5.04 3.42,5.52 3.65,6.27 3.00,5.82 2.35,6.27 2.58,5.52 1.95,5.04 2.74,5.03" fill="#FFFFFF"/>' +
    '<polygon points="6.00,4.28 6.26,5.03 7.05,5.04 6.42,5.52 6.65,6.27 6.00,5.82 5.35,6.27 5.58,5.52 4.95,5.04 5.74,5.03" fill="#FFFFFF"/>' +
    '<polygon points="9.00,4.28 9.26,5.03 10.05,5.04 9.42,5.52 9.65,6.27 9.00,5.82 8.35,6.27 8.58,5.52 7.95,5.04 8.74,5.03" fill="#FFFFFF"/>' +
    '<polygon points="3.00,6.98 3.26,7.72 4.05,7.74 3.42,8.21 3.65,8.97 3.00,8.52 2.35,8.97 2.58,8.21 1.95,7.74 2.74,7.72" fill="#FFFFFF"/>' +
    '<polygon points="6.00,6.98 6.26,7.72 7.05,7.74 6.42,8.21 6.65,8.97 6.00,8.52 5.35,8.97 5.58,8.21 4.95,7.74 5.74,7.72" fill="#FFFFFF"/>' +
    '<polygon points="9.00,6.98 9.26,7.72 10.05,7.74 9.42,8.21 9.65,8.97 9.00,8.52 8.35,8.97 8.58,8.21 7.95,7.74 8.74,7.72" fill="#FFFFFF"/>' +
    '</svg>'),
};

/** 国旗图标：中美 = SVG 图片；聚合平台 = 🌐（普通 emoji，Win10 有彩色字形）。 */
const FlagIcon: React.FC<{ kind?: FlagKind }> = ({ kind }) => {
  if (kind === 'cn' || kind === 'us') {
    return <img className="mset-flag" src={FLAG_DATA_URI[kind]} width={18} height={12} alt="" />;
  }
  return <span className="mset-flag-globe">🌐</span>;
};

/** 供应商下拉排序：中国 → 美国 → 其他（聚合平台/未标注）。同组内保持 catalog 原始相对顺序。 */
const sortedProviders = [...PROVIDERS].sort((a, b) => {
  const rank = (slug: string): number => {
    const f = PROVIDER_FLAGS[slug];
    return f === 'cn' ? 0 : f === 'us' ? 1 : 2;
  };
  return rank(a.slug) - rank(b.slug);
});

interface Props {
  /** 已保存的绑定（sealed=true 时进入封存态）。null = 从未配置。 */
  binding: ModelBinding | null;
  /**
   * 「跟随全局」提示语（仅 per-Agent 场景传）：binding 为空但全局主模型存在时，
   * 封存态里展示全局的值 + 这句话，让用户知道此刻实际生效的是什么。
   */
  followNote?: string;
  /** followNote 场景下用于展示的全局绑定。 */
  followBinding?: ModelBinding | null;
  onSave: (b: { provider: string; model: string; apiKey: string }) => void;
  onEdit: () => void;
  /** 「确定」按钮旁的额外元素（比如「清除个性化」）。 */
  extraAction?: React.ReactNode;
}

/** Key 的星号占位展示（封存态不回显真 Key 长度也无妨——统一 12 个点）。 */
/* [v1.0.24.2] 掩码依据 hasApiKey 判定：后端不回传真 Key，apiKey 恒空，须凭「凭据存在」标志展示。 */
const KEY_MASK = '••••••••••••';

export const ModelBindingModule: React.FC<Props> = ({
  binding, followNote, followBinding, onSave, onEdit, extraAction,
}) => {
  const { t } = useTranslation();
  const sealed = !!binding?.sealed;
  // 跟随全局：自己没有绑定、但传了 followBinding —— 展示全局值的封存态。
  const following = !binding && !!followBinding;
  const shown = binding ?? followBinding ?? null;

  // ── 草稿态（编辑时用；binding 变化 → 重新播种）──
  const [provider, setProvider] = useState(shown?.provider ?? '');
  const [model, setModel] = useState(shown?.model ?? '');
  const [apiKey, setApiKey] = useState(shown?.apiKey ?? '');

  useEffect(() => {
    setProvider(shown?.provider ?? '');
    setModel(shown?.model ?? '');
    setApiKey(shown?.apiKey ?? '');
    // 封存态切换 / 换目标（per-Agent 换人）时，草稿跟着已存值走。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [binding, followBinding]);

  const frozen = sealed || following;
  const models = modelsOf(provider);
  const canSave = !!provider && !!model && !!apiKey.trim();

  // [v1.0.24.1] 确认后自动测试连接：结果展示在按钮右侧。
  // 只监听 provider/model：保存后播种草稿时 apiKey 会被清空（后端不回传 key），
  // 若监听 apiKey 会把刚显示的测试结果立即清掉（曾踩坑）；换 agent / 改厂商模型才清。
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  useEffect(() => { setTestResult(null); }, [provider, model]);

  const handleConfirm = (): void => {
    onSave({ provider, model, apiKey: apiKey.trim() });
    const payload = { provider, model, apiKey: apiKey.trim() };
    setTesting(true);
    setTestResult(null);
    void testBinding(payload)
      .then((r) => setTestResult(r))
      .catch(() => setTestResult({ ok: false, message: i18n.t('settings.10') }))
      .finally(() => setTesting(false));
  };

  // ③ 前半部分（厂商/模型）修改后自动清空 Key（README 原话）。
  const changeProvider = (slug: string): void => {
    setProvider(slug);
    setModel('');
    setApiKey('');
  };
  const changeModel = (m: string): void => {
    setModel(m);
    setApiKey('');
  };

  // ── 提供商自定义下拉（原生 select 的 option 只能放文本，放不了国旗图片）──
  const [providerOpen, setProviderOpen] = useState(false);
  const providerBoxRef = React.useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!providerOpen) return;
    const onDocMouseDown = (e: MouseEvent): void => {
      if (providerBoxRef.current && !providerBoxRef.current.contains(e.target as Node)) {
        setProviderOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [providerOpen]);

  // ── 模型自定义下拉（与提供商下拉同款视觉；模型名不带国旗）──
  const [modelOpen, setModelOpen] = useState(false);
  const modelBoxRef = React.useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!modelOpen) return;
    const onDocMouseDown = (e: MouseEvent): void => {
      if (modelBoxRef.current && !modelBoxRef.current.contains(e.target as Node)) {
        setModelOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [modelOpen]);

  const providerSlug = frozen ? (shown?.provider ?? '') : provider;
  const providerEntry = PROVIDERS.find((p) => p.slug === providerSlug);
  const modelSlug = frozen ? (shown?.model ?? '') : model;
  const modelOptions = frozen ? modelsOf(shown?.provider ?? '') : models;

  return (
    <div className={'mset' + (frozen ? ' sealed' : '')}>
      <div className="set-item">
        <div className="si-body">
          <div className="si-t">{t('model.binding.module.02')}</div>
          <div className="si-d">{t('model.binding.module.13')}</div>
        </div>
        <div className="set-ctrl">
          <div className="mset-select" ref={providerBoxRef}>
            <button
              type="button"
              className="field mset-field mset-select-trigger"
              disabled={frozen}
              onClick={() => setProviderOpen((v) => !v)}
              aria-haspopup="listbox"
              aria-expanded={providerOpen}
              aria-label={t('model.binding.module.02')}
            >
              {providerEntry ? (
                <>
                  <FlagIcon kind={PROVIDER_FLAGS[providerEntry.slug]} />
                  <span className="mset-select-label">
                    {!i18n.language.startsWith('en') && PROVIDER_CN_NAMES[providerEntry.slug] ? `${t(PROVIDER_CN_NAMES[providerEntry.slug] ?? '')} ` : ''}
                    {providerEntry.label}
                  </span>
                </>
              ) : (
                <span>{t('model.binding.module.05')}</span>
              )}
              <span className="mset-select-caret" aria-hidden>▾</span>
            </button>
            {providerOpen && (
              <div className="mset-select-list" role="listbox" aria-label={t('model.binding.module.02')}>
                <div
                  role="option"
                  aria-selected={providerSlug === ''}
                  className={'mset-select-opt' + (providerSlug === '' ? ' sel' : '')}
                  onClick={() => { changeProvider(''); setProviderOpen(false); }}
                >
                  {t('model.binding.module.05')}
                </div>
                {sortedProviders.map((p) => (
                  <div
                    key={p.slug}
                    role="option"
                    aria-selected={providerSlug === p.slug}
                    className={'mset-select-opt' + (providerSlug === p.slug ? ' sel' : '')}
                    onClick={() => { changeProvider(p.slug); setProviderOpen(false); }}
                  >
                    <FlagIcon kind={PROVIDER_FLAGS[p.slug]} />
                    <span>
                      {!i18n.language.startsWith('en') && PROVIDER_CN_NAMES[p.slug] ? `${t(PROVIDER_CN_NAMES[p.slug] ?? '')} ` : ''}
                      {p.label}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="set-item">
        <div className="si-body">
          <div className="si-t">{t('common.18')}</div>
          <div className="si-d">{t('model.binding.module.12')}</div>
        </div>
        <div className="set-ctrl">
          <div className="mset-select" ref={modelBoxRef}>
            <button
              type="button"
              className="field mset-field mset-select-trigger"
              disabled={frozen || !providerSlug}
              onClick={() => setModelOpen((v) => !v)}
              aria-haspopup="listbox"
              aria-expanded={modelOpen}
              aria-label={t('common.18')}
            >
              <span className="mset-select-model">
                {modelSlug || (providerSlug ? t('model.binding.module.05') : '')}
              </span>
              <span className="mset-select-caret" aria-hidden>▾</span>
            </button>
            {modelOpen && (
              <div className="mset-select-list" role="listbox" aria-label={t('common.18')}>
                <div
                  role="option"
                  aria-selected={modelSlug === ''}
                  className={'mset-select-opt' + (modelSlug === '' ? ' sel' : '')}
                  onClick={() => { changeModel(''); setModelOpen(false); }}
                >
                  {t('model.binding.module.05')}
                </div>
                {modelOptions.map((m) => (
                  <div
                    key={m}
                    role="option"
                    aria-selected={modelSlug === m}
                    className={'mset-select-opt' + (modelSlug === m ? ' sel' : '')}
                    onClick={() => { changeModel(m); setModelOpen(false); }}
                  >
                    {m}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="set-item">
        <div className="si-body">
          <div className="si-t">API Key</div>
          <div className="si-d">{t('model.binding.module.07')}</div>
        </div>
        <div className="set-ctrl">
          <div className="field pw mset-field">
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder={frozen ? '' : t('model.binding.module.10')}
              value={frozen ? (shown && (shown.hasApiKey === true || shown.apiKey) ? KEY_MASK : '') : apiKey}
              disabled={frozen}
              onChange={(e) => { setApiKey(e.target.value); setTestResult(null); }} // [v1.0.24.1] 改 key 旧测试结果失效
              // ③ 可粘贴不可复制：拦下 copy / cut（paste 放行）。
              onCopy={(e) => e.preventDefault()}
              onCut={(e) => e.preventDefault()}
              aria-label="API Key"
            />
          </div>
        </div>
      </div>

      {following && followNote && <div className="mset-follow">{followNote}</div>}

      <div className="mset-actions">
        {frozen ? (
          <button type="button" className="test-btn" onClick={onEdit}>{t('model.binding.module.01')}</button>
        ) : (
          <button
            type="button"
            className="test-btn mset-ok"
            disabled={!canSave}
            onClick={handleConfirm}
          >
            {t('model.binding.module.confirm')}
          </button>
        )}
        {extraAction}
        {/* [v1.0.24.1] 确认后自动测试连接的结果：按钮右侧，多行显示，溢出省略 */}
        {testing && <span className="mset-test-res testing">{t('settings.view.16')}…</span>}
        {!testing && testResult && (
          <span className={'mset-test-res ' + (testResult.ok ? 'ok' : 'err')} title={testResult.message}>
            {testResult.ok ? '✅ ' : '⚠️ '}
            {testResult.message}
            {testResult.ok && testResult.latencyMs != null ? ` · ${testResult.latencyMs}ms` : ''}
          </span>
        )}
      </div>
    </div>
  );
};

export default ModelBindingModule;
