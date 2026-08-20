/**
 * [v1.0.13][R1] First-run entry requires test→apply acknowledgement for one fingerprint.
 * FirstRunModelGate.tsx — [v0.44.1 Bug3] 首次启动·强制模型引导
 *
 * 摆放位置：src/components/FirstRunModelGate.tsx
 * 挂载点：App.tsx 顶层（所有视图与浮层之上）。
 *
 * ── 为什么有它 ──
 *   Bug3 移除了后端代码里写死的 DeepSeek 默认模型/Key，模型的唯一配置入口收敛到
 *   「设置 → 模型与提供方」。既然没有隐藏默认，那么**软件第一次打开、还没有配置全局
 *   主模型时**，必须先把用户拦在门口，强制走完一遍：
 *       选择提供商 → 选择模型 → 输入 Key → 连接测试（必须通过）→ 确认进入
 *   完成前，背景雾化、其余 UI 一律不可交互（scrim 铺满并吃掉所有指针事件）。
 *
 * ── UI 语言 ──
 *   雾化遮罩（backdrop-filter 毛玻璃）+ 居中弹窗卡片，与既有 .scrim/弹窗一脉相承；
 *   卡片内的「厂商/模型/Key」表单**直接复用 ModelBindingModule**（与设置面板逐像素一致），
 *   连接测试复用 settings 的 testModelConnection（真打后端 /settings/test，禁 mock）。
 *
 * ── 状态机 ──
 *   firstRun：进程这次启动时「有没有已封存的主模型」的快照。false → 永不显示（老用户直接进）。
 *   随后由本地状态驱动，**不因中途 saveMainModel 封存就提前消失**——必须测通 + 点「进入」。
 *     · 主模型未封存        → 停在「配置」步：ModelBindingModule 可编辑，测试/进入禁用
 *     · 已封存、未测通      → 「连接测试」可点；点「修改」会解封 → 测试作废、退回配置步
 *     · 测试通过           → 「进入 Knowe」亮起；点它 → entered=true → 卡片退场
 */

import React, { useEffect, useState } from 'react';
import {
  useSettingsStore, testModelConnection, type TestResult,
} from '../store/settings';
import ModelBindingModule from './ModelBindingModule';
import PrimaryLanguageModule from './PrimaryLanguageModule'; // [v1.0.21.3]
import { featureEnabled } from '../shared/featureFlags';
import { useTranslation } from 'react-i18next';
import './first-run.css';

export const FirstRunModelGate: React.FC = () => {
  const { t } = useTranslation();
  const mainModel = useSettingsStore((s) => s.mainModel);
  const saveMainModel = useSettingsStore((s) => s.saveMainModel);
  const editMainModel = useSettingsStore((s) => s.editMainModel);
  const applyModelBinding = useSettingsStore((s) => s.applyModelBinding);
  const modelApplyState = useSettingsStore((s) => s.modelApplyState);
  const modelApplyError = useSettingsStore((s) => s.modelApplyError);
  const appliedFingerprint = useSettingsStore((s) => s.appliedFingerprint);
  const zinniaCompatible = useSettingsStore((s) => s.zinniaCompatible);

  // 这次启动是不是「首启」（还没有已封存的主模型）——只在挂载时判一次。
  const [firstRun] = useState(() => !useSettingsStore.getState().mainModel?.sealed);
  const [entered, setEntered] = useState(false);

  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);

  const sealed = !!mainModel?.sealed;
  // 已封存绑定的「身份」——一旦厂商/模型/Key 变了（用户点了修改再改），测试结果作废。
  const sig = sealed ? `${mainModel?.provider}|${mainModel?.model}|${mainModel?.apiKey}` : '';
  useEffect(() => {
    setResult(null);
  }, [sig]);

  // [v1.0.19.5] 保存即自动测试：onSave 封存后打标记，等 React 完成重渲染（sealed
  //   已更新）再触发 runTest——不能用 setTimeout 赌时序，渲染可能晚于宏任务。
  const pendingAutoTest = React.useRef(false);
  useEffect(() => {
    if (pendingAutoTest.current && sealed && !testing) {
      pendingAutoTest.current = false;
      void runTest();
    }
  }, [sealed, testing]);

  if (!firstRun || entered) return null;

  const readinessGate = featureEnabled('MODEL_READINESS_GATE_V1');
  const testedFingerprint = result?.testedFingerprint ?? null;
  const tested = !!result?.ok;
  const canEnter = readinessGate
    ? Boolean(
      sealed
      && tested
      && testedFingerprint
      && modelApplyState === 'applied'
      && appliedFingerprint === testedFingerprint
      && zinniaCompatible,
    )
    : sealed && tested;

  const runTest = async (): Promise<void> => {
    if (testing || !sealed) return;
    setTesting(true);
    setResult(null);
    try {
      const testedResult = await testModelConnection('main');
      if (!testedResult.ok) {
        setResult(testedResult);
        return;
      }
      if (!readinessGate) {
        setResult(testedResult);
        return;
      }
      if (!testedResult.testedFingerprint) {
        setResult({ ...testedResult, ok: false, message: t('first.run.model.gate.10') });
        return;
      }
      // The tested binding may be the main model while Zinnia resolves a separately
      // configured compatible auxiliary binding.  The POST /settings acknowledgement is
      // the authority for effective Zinnia compatibility; do not reject on probe metadata.
      const applied = await applyModelBinding(testedResult.testedFingerprint);
      setResult(applied.ok && applied.appliedFingerprint === testedResult.testedFingerprint
        ? { ...testedResult, message: t('first.run.model.gate.applied', { message: testedResult.message || t('first.run.model.gate.09') }) }
        : {
          ...testedResult,
          ok: false,
          message: applied.message || t('first.run.model.gate.07'),
        });
    } finally {
      setTesting(false);
    }
  };

  const stepConfig = !sealed;            // 步骤 1：还在配置
  const stepTest = sealed && !canEnter;  // 步骤 2：测试并等待 apply ack
  const stepDone = canEnter;             // 步骤 3：可进入

  return (
    <div className="frg-scrim" role="dialog" aria-modal="true" aria-label={t('first.run.model.gate.11')}>
      <div className="frg-card">
        <div className="frg-head">
          <div className="frg-badge">Knowe知知智能体</div>
          <h2 className="frg-title">{t('first.run.model.gate.06')}</h2>
          <p className="frg-sub">
            {t('first.run.model.gate.intro')}
          </p>
          <div className="frg-steps">
            <span className={'frg-step' + (stepConfig ? ' on' : ' done')}>{t('first.run.model.gate.02')}</span>
            <span className="frg-arrow">›</span>
            <span className={'frg-step' + (stepTest ? ' on' : canEnter ? ' done' : '')}>{t('first.run.model.gate.03')}</span>
            <span className="frg-arrow">›</span>
            <span className={'frg-step' + (stepDone ? ' on' : '')}>{t('first.run.model.gate.04')}</span>
          </div>
        </div>

        {/* [v1.0.21.3] 主要语言模块：首次安装配置卡片上方（紧凑模式） */}
        <div className="frg-lang">
          <PrimaryLanguageModule compact />
        </div>

        <div className="frg-body">
          <ModelBindingModule
            binding={mainModel}
            onSave={(b) => {
              saveMainModel(b);
              // [v1.0.19.5] 保存即自动测试（与设置页同款）：封存配置后自动验证连接，
              //   用户无需手动点「测试并应用」。实际触发在 useEffect 里（等渲染完成）。
              pendingAutoTest.current = true;
            }}
            onEdit={editMainModel}
          />

          <div className="frg-test">
            <button
              type="button"
              className="test-btn"
              disabled={!sealed || testing || modelApplyState === 'pending'}
              onClick={() => { void runTest(); }}
            >
              {t('first.run.model.gate.testApply')}
            </button>
            <span className={'frg-result' + (result ? (result.ok ? ' ok' : ' err') : '')}>
              {testing || modelApplyState === 'pending'
                ? (modelApplyState === 'pending' ? t('first.run.model.gate.09') : t('first.run.model.gate.01'))
                : result
                  ? `${result.message}${result.ok && result.latencyMs != null ? ` · ${result.latencyMs}ms` : ''}`
                  : modelApplyError
                    ? modelApplyError
                    : (sealed ? t('first.run.model.gate.08') : t('first.run.model.gate.05'))}
            </span>
          </div>
        </div>

        <div className="frg-foot">
          <button
            type="button"
            className="test-btn frg-enter"
            disabled={!canEnter}
            onClick={() => { if (canEnter) setEntered(true); }}
          >
            {t('first.run.model.gate.enter')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default FirstRunModelGate;
