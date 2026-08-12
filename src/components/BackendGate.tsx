/**
 * [v1.0.13][R1] Reconcile persisted model readiness on every backend-ready transition.
 * BackendGate.tsx — 后端状态指示器.
 *
 * 后端是这个应用的心跳。它停了，界面上的一切都是死的——但用户看到的只会是
 * 「消息发不出去」，而不知道为什么。这个组件就是把「为什么」摆到台面上。
 *
 * 显示规则（安静优先——正常的时候一个像素都不占）：
 *   ready              → 什么都不显示
 *   starting           → 标题栏右侧一行灰字「后端启动中…」
 *   crashed / failed   → 醒目的「后端已断开」+ 重试按钮
 *   stopped            → 什么都不显示（正在退出应用，别在这时候吓人）
 *
 * ★ 浏览器兜底：`window.knowe` 不存在时（非 Electron），**渲染为空，且不报错**。
 *   前端必须能在浏览器里单跑——这是联调时最常用的姿势。
 */

import React, { useEffect, useRef, useState } from 'react';
import type { BackendStatus } from '../shared/bridge';
import { useSettingsStore } from '../store/settings';
import { useTranslation } from 'react-i18next';

export const BackendGate: React.FC = () => {
  const { t } = useTranslation();
  const bridge = typeof window === 'undefined' ? undefined : window.knowe;
  // [阶段一 1.5] 正式版（打包安装版）判定。
  //   ★ 关键：这里只隐藏**显示**，不中断**副作用**——「后端 ready → reconcileFromBackend
  //   （设置同步）」在下方 useEffect 里照常执行，正式版也必须同步配置。
  const isPackaged = bridge?.isPackaged === true;

  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [restarting, setRestarting] = useState(false);
  const previousPhase = useRef<BackendStatus['phase'] | null>(null);

  useEffect(() => {
    if (!bridge) return;   // 浏览器模式：不订阅、不请求、不报错

    let alive = true;
    const accept = (next: BackendStatus): void => {
      if (!alive) return;
      const becameReady = next.phase === 'ready' && previousPhase.current !== 'ready';
      previousPhase.current = next.phase;
      setStatus(next);
      if (becameReady) void useSettingsStore.getState().reconcileFromBackend();
      // 后端起来了，重试按钮的「重试中…」就该收了
      if (next.phase === 'ready' || next.phase === 'stopped') setRestarting(false);
    };
    void bridge.getBackendStatus().then(accept);
    const off = bridge.onBackendStatus(accept);

    return () => { alive = false; off(); };
  }, [bridge]);

  // 不在 Electron 里 / 还没拿到状态 / 后端好着呢 → 一个像素都不占
  if (!bridge || !status) return null;
  // [阶段一 1.5] 正式版：故障提示 UI（「后端已断开」+重试按钮）一律不渲染；
  //   「设置同步」副作用已在上方 useEffect 保留执行（只隐藏显示，不断副作用）。
  if (isPackaged) return null;
  if (status.phase === 'ready' || status.phase === 'stopped') return null;

  if (status.phase === 'starting') {
    return <span className="bg-hint">{t('backend.gate.01')}…</span>;
  }

  const retry = (): void => {
    setRestarting(true);
    void bridge.restartBackend()
      .then((s) => setStatus(s))
      // ★ 无论成败都要解锁按钮：重启又失败时不解锁的话，
      //   按钮就永远卡在「重试中…」，用户再也点不动了（测试逮到过这个 bug）。
      //   如果重启成功，整个组件会因为 phase=ready 而消失，解锁与否无所谓。
      .finally(() => setRestarting(false));
  };

  return (
    <div className="bg-down" role="status" aria-live="polite">
      <span className="bg-dot" aria-hidden="true" />
      <span className="bg-text">
        {t('backend.gate.03')}
        {status.message ? <span className="bg-why">{status.message}</span> : null}
      </span>
      <button
        type="button"
        className="btn btn-ghost bg-retry"
        onClick={retry}
        disabled={restarting}
      >
        {restarting ? t('backend.gate.02') + '…' : t('common.03')}
      </button>
    </div>
  );
};

export default BackendGate;
