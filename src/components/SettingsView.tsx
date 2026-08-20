/**
 * SettingsView.tsx — [v0.44] 设置视图
 *
 * 摆放位置：src/components/SettingsView.tsx
 *
 * 挂载点：App.tsx 的 .view-alt（activeView === 'settings'）。
 * 布局：与 ContactsView / FavoritesView / KnowledgeView 同款——左 .side（此处为 .set-nav
 * 导航列表）+ 右 .stage > .stage-card > .set-pane。排版骨架照抄参考 HTML 的设置区域
 * （.set-nav / .set-pane / .set-block / .set-item / .switch / .field / .test-btn / .test-res，
 * 样式在 settings-view.css），**内容按 v0.44 README 定制**：
 *
 *   导航五项（README §一：Agent 与项目 / 隐私与数据 / 快捷键 三项删除）：
 *     账户与身份 · 模型与提供方 · 通知 · 外观 · 关于
 *
 *   §2.2 温度设置本版本不做——UI 里没有任何温度相关控件（README §四 约束）。
 *   §2.4 深色只留接口（store.appearance → App 挂 theme-dark 类，CSS 暂空）。
 */

import React, { useEffect, useRef, useState } from 'react';
import type { BackendStatus, UpdateStatus } from '../shared/bridge';
import { useKnoweStore } from '../store/store';
import {
  useSettingsStore, testModelConnection, effectiveAuxBinding,
  APPROVAL_TIMEOUT_OPTIONS, type ModelApplyResult, type TestResult,
} from '../store/settings';
import { providerLabel } from '../store/modelCatalog';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import ModelBindingModule from './ModelBindingModule';
import PrimaryLanguageModule from './PrimaryLanguageModule'; // [v1.0.21.3]
import MSetSelect from './MSetSelect'; // [v1.0.24.1] 设置内下拉统一为 mset 样式
import { toast } from './ContextMenu';
import './settings-view.css';

const SECTIONS = ['账户与身份', '模型与提供方', '通知', '外观', '关于'] as const;
export type SettingsSection = (typeof SECTIONS)[number];

export interface SettingsSearchFocus {
  section: SettingsSection;
  requestId: number;
}

/** [v1.0.25.4] window.knowe 的类型化访问（老 preload 缺字段时全部 ?. 容错）。 */
function bridge(): Window['knowe'] | undefined {
  return (window as unknown as { knowe?: Window['knowe'] }).knowe;
}

function toastApplyResult(result: ModelApplyResult): void {
  if (!result.ok || result.restartRequired) toast(result.message, 'warn');
  else toast(result.message);
}

function restartSettled(status: BackendStatus): boolean {
  return status.phase === 'ready'
    || status.phase === 'failed'
    || status.phase === 'crashed'
    || status.phase === 'stopped';
}

/** IPC restart returns the immediate `starting` state; wait for the already-whitelisted status channel. */
function waitForRestartResult(
  bridge: NonNullable<Window['knowe']>,
  initial: BackendStatus,
): Promise<BackendStatus> {
  if (restartSettled(initial)) return Promise.resolve(initial);
  return new Promise((resolve) => {
    let latest = initial;
    let settled = false;
    let unsubscribe: () => void = () => undefined;
    const finish = (status: BackendStatus): void => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      unsubscribe();
      resolve(status);
    };
    const observe = (status: BackendStatus): void => {
      latest = status;
      if (restartSettled(status)) finish(status);
    };
    const timeout = window.setTimeout(() => finish(latest), 45_000);
    unsubscribe = bridge.onBackendStatus(observe);
    void bridge.getBackendStatus().then(observe).catch(() => undefined);
  });
}

// ═══════════════════════════════════════════════════════════════
// 小构件
// ═══════════════════════════════════════════════════════════════

/** 参考 HTML 的 switchEl：.switch(.on)，点击翻转。 */
const Switch: React.FC<{ on: boolean; onToggle: (on: boolean) => void; label: string }> = ({
  on, onToggle, label,
}) => (
  <div
    className={'switch' + (on ? ' on' : '')}
    role="switch"
    aria-checked={on}
    aria-label={label}
    tabIndex={0}
    onClick={() => onToggle(!on)}
    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(!on); } }}
  />
);

/** 参考 HTML 的 setItem：.set-item > .si-body(.si-t + .si-d) + .set-ctrl。 */
const SetItem: React.FC<{ t: string; d?: string; children?: React.ReactNode }> = ({ t, d, children }) => (
  <div className="set-item">
    <div className="si-body">
      <div className="si-t">{t}</div>
      {d && <div className="si-d">{d}</div>}
    </div>
    {children && <div className="set-ctrl">{children}</div>}
  </div>
);

// ═══════════════════════════════════════════════════════════════
// §2.1 账户与身份
// ═══════════════════════════════════════════════════════════════

/**
 * 头像上传：解码图片 → canvas 缩到 256px 内 → PNG dataURL（localStorage 装得下）。
 *
 * [v0.44.1 Bug1] 老实现走 `FileReader.readAsDataURL → img.src = dataURL` 这条链：
 *   在 Electron（webSecurity:true）里，把一条 data: URL 塞给 <img> 解码，最容易踩两个坑——
 *     ① CSP 的 img-src 若没放行 data:（默认回落 default-src 'self' 就会这样），<img> 直接
 *        触发 onerror；
 *     ② 部分格式/大图在 <img> 这条解码路径上时好时坏。
 *   两者最终都落到 `img.onerror → toast('图片读取失败')`，也就是用户看到的现象。
 *
 * 改用 createImageBitmap(file) 直接解码 Blob：不经过 <img>、不碰 img-src，解码更稳、
 *   也省掉一次「先编码成 dataURL 再解码」的来回。只有在没有 createImageBitmap 的老环境
 *   才回落到 objectURL + <img>（用 blob: 而非 data:，并用完即撤）。
 */
function readAvatarFile(file: File, onDone: (dataUrl: string) => void): void {
  if (!file.type.startsWith('image/')) {
    toast(i18n.t('settings.view.pickImage'), 'warn');
    return;
  }

  const MAX = 256;

  /** 把已解码的图源画进 canvas，缩到 MAX 内，导出 PNG dataURL；失败返回 null。 */
  const toScaledDataUrl = (src: CanvasImageSource, w: number, h: number): string | null => {
    if (!w || !h) return null;
    const scale = Math.min(1, MAX / Math.max(w, h));
    const cw = Math.max(1, Math.round(w * scale));
    const ch = Math.max(1, Math.round(h * scale));
    const canvas = document.createElement('canvas');
    canvas.width = cw;
    canvas.height = ch;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(src, 0, 0, cw, ch);
    try {
      return canvas.toDataURL('image/png');
    } catch {
      return null;
    }
  };

  /** 兜底：老环境没有 createImageBitmap → objectURL + <img>（blob:，非 data:）。 */
  const fallbackViaImg = (): void => {
    const objUrl = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const url = toScaledDataUrl(img, img.naturalWidth || img.width, img.naturalHeight || img.height);
      URL.revokeObjectURL(objUrl);
      if (url) onDone(url);
      else toast('图片读取失败', 'warn');
    };
    img.onerror = () => {
      URL.revokeObjectURL(objUrl);
      toast(i18n.t('settings.view.imageReadFailed'), 'warn');
    };
    img.src = objUrl;
  };

  if (typeof createImageBitmap === 'function') {
    createImageBitmap(file)
      .then((bmp) => {
        const url = toScaledDataUrl(bmp, bmp.width, bmp.height);
        bmp.close?.();
        if (url) onDone(url);
        else fallbackViaImg();     // 解码成了但 canvas 导出失败 → 再试一条路
      })
      .catch(() => fallbackViaImg());
    return;
  }

  fallbackViaImg();
}

const AccountPane: React.FC = () => {
  const { t } = useTranslation();
  const userName = useSettingsStore((s) => s.userName);
  const avatarDataUrl = useSettingsStore((s) => s.avatarDataUrl);
  const setUserName = useSettingsStore((s) => s.setUserName);
  const setAvatar = useSettingsStore((s) => s.setAvatar);
  const pushToBackend = useSettingsStore((s) => s.pushToBackend);
  const fileRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(userName);

  useEffect(() => { setDraft(userName); }, [userName]);

  const commitName = async (): Promise<void> => {
    const name = draft.trim();
    if (!name) { setDraft(userName); return; }
    if (name !== userName) {
      setUserName(name);
      toastApplyResult(await pushToBackend());
    }
  };

  return (
    <>
      <div className="set-block">
        <h4>{t('global.search.04')}</h4>
        <div className="set-item">
          <div className="si-body set-acct-row">
            <button
              type="button"
              className="set-avatar"
              onClick={() => fileRef.current?.click()}
              title={t('settings.view.23')}
              aria-label={t('settings.view.04')}
            >
              {avatarDataUrl
                ? <img src={avatarDataUrl} alt={t('settings.view.26')} />
                : <span className="set-avatar-glyph">{(userName || '我').slice(0, 1)}</span>}
            </button>
            <div>
              <div className="si-t">{userName}</div>
              <div className="si-d">{t('settings.view.24')}</div>
            </div>
          </div>
          <div className="set-ctrl">
            {avatarDataUrl && (
              <button type="button" className="test-btn" onClick={() => setAvatar(null)}>
                {t('settings.view.removeAvatar')}
              </button>
            )}
          </div>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) readAvatarFile(f, (url) => { setAvatar(url); toast(t('settings.view.avatarUpdated')); });
            e.target.value = '';   // 允许连选同一张
          }}
        />
      </div>

      <div className="set-block">
        <h4>{t('settings.view.28')}</h4>
        <SetItem t={t('settings.view.27')} d={t('settings.view.07')}>
          {/* [v1.0.24.1] 右侧加【确认】按钮：点击 = 提交（同回车/失焦）；回车仍走 blur → commitName */}
          <div className="mset-testrow">
            <input
              className="field mset-field"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => { void commitName(); }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
              maxLength={24}
              aria-label={t('settings.view.27')}
            />
            <button
              type="button"
              className="test-btn"
              onClick={() => { void commitName(); }}
              aria-label={t('settings.view.27') + t('common.20')}
            >
              {t('common.20')}
            </button>
          </div>
        </SetItem>
      </div>

      {/* [v1.0.23.4] 主要语言模块：从模型与提供方移到账户与身份（语言是个人账户偏好） */}
      <div className="set-block">
        <PrimaryLanguageModule />
      </div>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════
// §2.2 模型与提供方
// ═══════════════════════════════════════════════════════════════

const ModelsPane: React.FC = () => {
  const { t } = useTranslation();
  const mainModel = useSettingsStore((s) => s.mainModel);
  const auxModel = useSettingsStore((s) => s.auxModel);
  const saveMainModel = useSettingsStore((s) => s.saveMainModel);
  const editMainModel = useSettingsStore((s) => s.editMainModel);
  const saveAuxModel = useSettingsStore((s) => s.saveAuxModel);
  const editAuxModel = useSettingsStore((s) => s.editAuxModel);
  const pushToBackend = useSettingsStore((s) => s.pushToBackend);

  const [testTarget, setTestTarget] = useState<'main' | 'aux'>('main');
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const testRunId = useRef(0);

  /**
   * 连接测试结果只对“发起测试那一刻”的绑定有效。
   *
   * 旧实现保存新厂商后仍保留上一次的绿色/红色结果；更隐蔽的是，若切换发生在请求
   * 途中，旧请求回来还会覆盖新配置旁边的状态。用 run id 同时清掉旧结果并废弃在途回包。
   */
  const invalidateTestResult = (): void => {
    testRunId.current += 1;
    setTesting(false);
    setResult(null);
  };

  useEffect(() => {
    testRunId.current += 1;
    setTesting(false);
    setResult(null);
    // 每个字段都是连接测试输入；任一变化，旧结果都不再具有证明力。
  }, [
    mainModel?.provider, mainModel?.model, mainModel?.apiKey, mainModel?.hasApiKey,
    mainModel?.clearApiKey, mainModel?.sealed,
    auxModel?.provider, auxModel?.model, auxModel?.apiKey, auxModel?.hasApiKey,
    auxModel?.clearApiKey, auxModel?.sealed,
  ]);

  const runTest = async (target: 'main' | 'aux' = testTarget): Promise<void> => {
    if (testing) return;
    const runId = testRunId.current + 1;
    testRunId.current = runId;
    setTesting(true);
    setResult(null);
    try {
      const r = await testModelConnection(target);
      if (runId === testRunId.current) setResult(r);
    } finally {
      if (runId === testRunId.current) setTesting(false);
    }
  };

  const auxEffective = effectiveAuxBinding(mainModel, auxModel);

  return (
    <>
      <div className="set-block">
        <h4>{t('global.search.01')}</h4>
        <div className="set-note">{t('settings.view.14')}</div>
        <ModelBindingModule
          binding={mainModel}
          onSave={(b) => {
            invalidateTestResult();
            saveMainModel(b);
            void pushToBackend().then((r) => {
              toastApplyResult(r);
              // v1.0.19.5: 保存即自动测试——用户无需手动点「测试」，结果直接展示。
              if (r.ok) void runTest('main');
            });
          }}
          onEdit={() => { invalidateTestResult(); editMainModel(); }}
        />
        {mainModel?.hasApiKey && !mainModel.apiKey && (
          <div className="mset-key-status" role="status">
            <span>{t('settings.view.01')}</span>
            <button
              type="button"
              className="test-btn mset-key-clear"
              onClick={() => {
                invalidateTestResult();
                saveMainModel({
                  provider: mainModel.provider,
                  model: mainModel.model,
                  apiKey: '',
                  hasApiKey: false,
                  clearApiKey: true,
                });
                void pushToBackend().then(toastApplyResult);
              }}
            >
              {t('settings.view.clearSavedKey')}
            </button>
          </div>
        )}
      </div>

      <div className="set-block">
        <h4>{t('global.search.13')}</h4>
        <ModelBindingModule
          binding={auxModel}
          followBinding={auxEffective}
          followNote={t('settings.view.auxFollowNote')}
          onSave={(b) => {
            invalidateTestResult();
            saveAuxModel(b);
            void pushToBackend().then((r) => {
              toastApplyResult(r);
              // v1.0.19.5: 保存即自动测试。
              if (r.ok) void runTest('aux');
            });
          }}
          onEdit={() => { invalidateTestResult(); editAuxModel(); }}
        />
        {auxModel?.hasApiKey && !auxModel.apiKey && (
          <div className="mset-key-status" role="status">
            <span>{t('settings.view.01')}</span>
            <button
              type="button"
              className="test-btn mset-key-clear"
              onClick={() => {
                invalidateTestResult();
                saveAuxModel({
                  provider: auxModel.provider,
                  model: auxModel.model,
                  apiKey: '',
                  hasApiKey: false,
                  clearApiKey: true,
                });
                void pushToBackend().then(toastApplyResult);
              }}
            >
              {t('settings.view.clearSavedKey')}
            </button>
          </div>
        )}
        <div className="set-note">
          {t('settings.view.auxModelHint')}
          {!auxModel?.sealed && auxEffective?.derived && (
            t('settings.view.auxEffective', { provider: providerLabel(auxEffective.provider), model: auxEffective.model })
          )}
          。
        </div>
      </div>

      <div className="set-block">
        <h4>{t('settings.view.33')}</h4>
        <SetItem t={t('settings.view.20')} d={t('settings.view.34')}>
          <div className="mset-testrow">
            <MSetSelect
              value={testTarget}
              options={[
                { value: 'main' as const, label: t('global.search.01') },
                { value: 'aux' as const, label: t('global.search.13') },
              ]}
              onChange={(v) => {
                invalidateTestResult();
                setTestTarget(v);
              }}
              ariaLabel={t('settings.view.20')}
            />
            <button type="button" className="test-btn" onClick={() => { void runTest(); }} disabled={testing}>
              {t('contacts.view.21')}
            </button>
          </div>
        </SetItem>
        {/* [v0.44.5 Bug1] 结果/错误提示独占一整行、整块换行；不再挤进右侧控件列，
            长错误文字（HTTP 401 等）也绝不会把左侧「测试目标」标签压成竖排。 */}
        {(testing || result) && (
          <div
            className={'test-res-line' + (result ? (result.ok ? ' ok' : ' err') : '')}
            role="status"
            aria-live="polite"
          >
            {testing ? (
              <>
                <span className="spinner" aria-label={t('first.run.model.gate.01')} />
                <span className="test-res-msg">{t('settings.view.16')}</span>
              </>
            ) : result ? (
              <span className="test-res-msg">
                {result.message}
                {result.ok && result.latencyMs != null ? ` · ${result.latencyMs}ms` : ''}
              </span>
            ) : null}
          </div>
        )}
      </div>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════
// §2.3 通知
// ═══════════════════════════════════════════════════════════════

const NotifyPane: React.FC = () => {
  const { t } = useTranslation();
  const notifyDesktop = useSettingsStore((s) => s.notifyDesktop);
  const closeToTray = useSettingsStore((s) => s.closeToTray);
  const approvalTimeoutS = useSettingsStore((s) => s.approvalTimeoutS);
  const setNotifyDesktop = useSettingsStore((s) => s.setNotifyDesktop);
  const setCloseToTray = useSettingsStore((s) => s.setCloseToTray);
  const setApprovalTimeout = useSettingsStore((s) => s.setApprovalTimeout);
  const pushToBackend = useSettingsStore((s) => s.pushToBackend);

  return (
    <>
      <div className="set-block">
        <h4>{t('settings.view.21')}</h4>
        <SetItem t={t('global.search.06')} d={t('settings.view.05')}>
          <Switch on={notifyDesktop} onToggle={setNotifyDesktop} label={t('global.search.06')} />
        </SetItem>
        <SetItem t={t('settings.view.10')} d={t('settings.view.25')}>
          <Switch on={closeToTray} onToggle={setCloseToTray} label={t('settings.view.10')} />
        </SetItem>
      </div>

      <div className="set-block">
        <h4>{t('knowledge.view.02')}</h4>
        <SetItem t={t('contacts.view.09')} d={t('settings.view.08')}>
          <MSetSelect
            value={approvalTimeoutS}
            options={APPROVAL_TIMEOUT_OPTIONS.map((o) => ({ value: o.value, label: t(o.label) }))}
            onChange={(v) => {
              setApprovalTimeout(v);
              void pushToBackend().then(toastApplyResult);
            }}
            ariaLabel={t('contacts.view.09')}
          />
        </SetItem>
      </div>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════
// §2.4 外观
// ═══════════════════════════════════════════════════════════════

const AppearancePane: React.FC = () => {
  const { t } = useTranslation();
  const appearance = useSettingsStore((s) => s.appearance);
  const fontScale = useSettingsStore((s) => s.fontScale);
  const setAppearance = useSettingsStore((s) => s.setAppearance);
  const setFontScale = useSettingsStore((s) => s.setFontScale);

  return (
    <div className="set-block">
      <h4>{t('global.search.03')}</h4>
      <SetItem t={t('settings.view.11')} d={t('settings.view.22')}>
        <MSetSelect
          value={appearance}
          options={[
            { value: 'light' as const, label: t('global.search.09') },
            { value: 'dark' as const, label: t('global.search.10') },
          ]}
          onChange={(v) => setAppearance(v)}
          ariaLabel={t('settings.view.11')}
        />
      </SetItem>
      <SetItem t={t('global.search.05')} d={t('settings.view.03')}>
        <MSetSelect
          value={fontScale}
          options={[
            { value: 'small' as const, label: t('settings.view.sizeSmall') },
            { value: 'large' as const, label: t('settings.view.sizeLarge') },
          ]}
          onChange={(v) => setFontScale(v)}
          ariaLabel={t('global.search.05')}
        />
      </SetItem>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// §2.7 关于
// ═══════════════════════════════════════════════════════════════

const AboutPane: React.FC = () => {
  const { t } = useTranslation();
  // [v1.0.25.4] 产品版本号由主进程注入（package.json productVersion），不再写死
  const shellVersion = bridge()?.version;
  // [v1.0.28 R5] 大版本号 = 版本号前两段（如 1.0.28 → 1.0），「桌面版」固定字样写死
  const majorVersion = shellVersion?.split('.').slice(0, 2).join('.');
  // [v1.0.25.4] 自动更新状态：挂载时查一次 + 订阅主进程推送
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus>({ state: 'idle', progress: 0 });

  useEffect(() => {
    const b = bridge();
    if (!b?.onUpdateStatusChanged) return;
    const unsubscribe = b.onUpdateStatusChanged(setUpdateStatus);
    void b.getUpdateStatus?.().then((s) => setUpdateStatus(s));
    return unsubscribe;
  }, []);

  // [v1.0.25.4] 升级完成后由安装器 --updated 拉起 → toast 提示
  useEffect(() => {
    const b = bridge();
    if (!b?.onJustUpdated) return;
    const unsubscribe = b.onJustUpdated(() => toast(t('settings.view.autoUpdateJustUpdated'), 'info'));
    return unsubscribe;
  }, [t]);

  const checkNow = (): void => {
    const b = bridge();
    if (!b?.checkForUpdates) return;
    setUpdateStatus({ state: 'checking', progress: 0 });
    void b.checkForUpdates();
  };

  const installNow = (): void => {
    const b = bridge();
    if (!b?.installUpdate) return;
    void b.installUpdate();
  };

  return (
    <div className="set-block about-pane">
      <h4>{t('settings.view.09')}</h4>
      {/* [v1.0.28 R5] 标题下方显示大版本号（桌面版v1.0），右侧 tag 显示详细小版本号 v1.0.28 */}
      <SetItem t={t('global.search.11')} d={majorVersion ? t('settings.view.desktopVersion', { version: majorVersion }) : undefined}>
        <span className="navrow-tag">{shellVersion ? `v${shellVersion}` : ''}</span>
      </SetItem>
      <SetItem t={t('settings.view.12')} d={t('settings.view.19')}>
        <a
          className="set-link"
          href="https://knowe-agent.online"
          target="_blank"
          rel="noreferrer"
        >
          {t('settings.view.siteLabel')}
        </a>
      </SetItem>
      <SetItem t={t('settings.view.githubTitle')} d={t('settings.view.13')}>
        <a
          className="set-link"
          href="https://github.com/HirezmingD/Knowe-agent-groupchat"
          target="_blank"
          rel="noreferrer"
        >
          github.com/HirezmingD/Knowe-agent-groupchat
        </a>
      </SetItem>
      {/* [v1.0.25.4] 自动更新（PRD）：手动检查 + 有新版下载完成时出现「重启安装更新」 */}
      <SetItem t={t('settings.view.15')} d={t('settings.view.30')}>
        <div className="set-update-row">
          <button
            type="button"
            className="test-btn"
            disabled={updateStatus.state === 'checking'}
            onClick={checkNow}
          >
            {t('settings.view.15')}
          </button>
          {updateStatus.state === 'ready' && (
            <button type="button" className="test-btn set-update-install" onClick={installNow}>
              {t('settings.view.restartInstall')}
            </button>
          )}
        </div>
        {updateStatus.state === 'downloading' && (
          <div className="set-update-hint">
            {t('settings.view.autoUpdateDownloading', { percent: Math.round(updateStatus.progress) })}
          </div>
        )}
        {/* [v1.0.26.2] 手动检查无新版 → 「当前已是最新版本」提示（主进程 2.5 秒后自动回 idle） */}
        {updateStatus.state === 'up-to-date' && (
          <div className="set-update-hint set-update-up-to-date">{t('settings.view.autoUpdateUpToDate')}</div>
        )}
        {updateStatus.state === 'error' && (
          <div className="set-update-hint set-update-error">{t('settings.view.autoUpdateError')}</div>
        )}
      </SetItem>
      <div className="set-item">
        <div className="si-body">
          <div className="si-t">{t('settings.view.06')}</div>
          <div className="si-d set-author">
            {t('settings.view.aboutText')}{' '}
            <a className="set-link" href={`mailto:${t('settings.view.aboutEmail')}`}>{t('settings.view.aboutEmail')}</a>
            {t('settings.view.aboutWechat')}
          </div>
        </div>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// 视图外壳：.set-nav + .stage/.set-pane
// ═══════════════════════════════════════════════════════════════

export interface SettingsViewProps {
  searchFocus?: SettingsSearchFocus | null;
  onSearchFocusDone?: (requestId: number) => void;
}

export const SettingsView: React.FC<SettingsViewProps> = ({
  searchFocus = null, onSearchFocusDone,
}) => {
  const { t } = useTranslation();
  const [section, setSection] = useState<SettingsSection>('账户与身份');
  const [restartBusy, setRestartBusy] = useState(false);

  // 分区显示名映射：SECTIONS 值是内部协议（与其他文件 switch 匹配），展示时才翻译。
  const SECTION_LABELS: Record<SettingsSection, string> = {
    '账户与身份': t('global.search.12'),
    '模型与提供方': t('global.search.07'),
    '通知': t('global.search.14'),
    '外观': t('global.search.03'),
    '关于': t('global.search.02'),
  };

  // 开机 / 进设置页先以后端持久状态对账；GET 只返回 has_api_key，不回传明文。
  const reconcileFromBackend = useSettingsStore((s) => s.reconcileFromBackend);
  const clearRestartRequired = useSettingsStore((s) => s.clearRestartRequired);
  const restartRequired = useSettingsStore((s) => s.restartRequired);
  const connected = useKnoweStore((s) => s.conn);
  useEffect(() => {
    if (connected === 'live') void reconcileFromBackend();
  }, [connected, reconcileFromBackend]);

  // 全局搜索从外部指定分区时，直接复用本组件已有的 section 状态。
  useEffect(() => {
    if (!searchFocus) return;
    setSection(searchFocus.section);
    onSearchFocusDone?.(searchFocus.requestId);
  }, [onSearchFocusDone, searchFocus]);

  const restartBackend = async (): Promise<void> => {
    if (restartBusy) return;
    const bridge = window.knowe;
    if (!bridge?.restartBackend) {
      toast(t('settings.view.restartDesktopOnly'), 'warn');
      return;
    }
    setRestartBusy(true);
    try {
      const immediate = await bridge.restartBackend();
      const status = await waitForRestartResult(bridge, immediate);
      if (status.phase === 'ready') {
        clearRestartRequired();
        await reconcileFromBackend();
        toast(t('settings.view.restartOk'));
      } else if (status.phase === 'failed' || status.phase === 'crashed') {
        toast(status.message || t('settings.view.02'), 'warn');
      } else {
        toast(status.message || t('settings.view.18'), 'info');
      }
    } catch {
      toast(t('settings.view.restartFailed'), 'warn');
    } finally {
      setRestartBusy(false);
    }
  };

  return (
    <>
      <aside className="side set-nav">
        <div className="side-head" style={{ paddingLeft: 12 }}>
          <div className="side-title">{t('common.12')}</div>
        </div>
        <div className="side-scroll">
          {SECTIONS.map((s) => (
            <div
              key={s}
              className={'navrow' + (section === s ? ' active' : '')}
              role="button"
              tabIndex={0}
              onClick={() => setSection(s)}
              onKeyDown={(e) => { if (e.key === 'Enter') setSection(s); }}
            >
              <span className="navrow-nm">{SECTION_LABELS[s]}</span>
            </div>
          ))}
        </div>
      </aside>

      <div className="stage">
        <div className="stage-card">
          <div className="set-pane">
            <h2>{SECTION_LABELS[section]}</h2>
            {restartRequired && (
              <div className="set-restart-banner" role="status" aria-live="polite">
                <div className="set-restart-copy">
                  <strong>{t('settings.view.31')}</strong>
                  <span>{t('settings.view.32')}</span>
                </div>
                <button
                  type="button"
                  className="test-btn set-restart-btn"
                  onClick={() => { void restartBackend(); }}
                  disabled={restartBusy}
                >
                  {restartBusy ? t('settings.view.17') : t('settings.view.29')}
                </button>
              </div>
            )}
            {section === '账户与身份' && <AccountPane />}
            {section === '模型与提供方' && <ModelsPane />}
            {section === '通知' && <NotifyPane />}
            {section === '外观' && <AppearancePane />}
            {section === '关于' && <AboutPane />}
          </div>
        </div>
      </div>
    </>
  );
};

export default SettingsView;
