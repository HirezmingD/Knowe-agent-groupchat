/**
 * DevCorridor.tsx — 诊断走廊（v0.3 全量重建）
 *
 * ★ 相对旧版最重要的一个改动：**删掉了它自己那条 WebSocket。**
 *
 *   旧走廊会独立 new WebSocket 连后端。但后端对同项目的新连接会用 close code 4001
 *   让旧连接让位——也就是说，你一打开走廊点「连接」，就可能把主界面的连接顶掉，
 *   主界面变「未连接」且不会自动重连。**一个诊断工具把被诊断对象搞坏了**，这没法要。
 *
 *   新走廊是一块**只读的观察台**：它看的是真实产品连接（App.tsx 里那条唯一的 socket）
 *   在 corridor.ts 里留下的痕迹。看的是真东西，且绝不干扰它。
 *
 *   故障注入（mute / gap / crash / 双解决）搬去了 FakeKnoweServer，在测试里跑——
 *   那才是注入故障该待的地方，不是产品运行时。
 *
 * 风格与其余组件一致：零 style={{}}，className 走 knowe-components.css 的 dc-* 命名。
 */

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  exportCorridorJSON,
  getCorridorState,
  resetCorridor,
  subscribeCorridor,
  type CorridorEntry,
  type CorridorState,
  type EventVerdict,
} from './corridor';
import { useKnoweStore } from '../store/store';
import { selectConn, selectActiveProjectId } from '../store/selectors';

// 判定 → 颜色档（CSS 里定义 dc-v-ok / dc-v-warn / dc-v-bad / dc-v-info）
const VERDICT_TONE: Record<EventVerdict, string> = {
  applied: 'ok',
  bypass: 'info',
  buffered: 'info',
  dup: 'warn',
  gap: 'bad',
  rejected: 'bad',
  sentinel: 'bad',
  epoch: 'info',
  failed: 'bad',
};

const VERDICT_TEXT: Record<EventVerdict, string> = {
  applied: 'dev.corridor.07',
  bypass: 'dev.corridor.09',
  buffered: 'dev.corridor.08',
  dup: 'dev.corridor.13',
  gap: 'dev.corridor.03',
  rejected: 'dev.corridor.01',
  sentinel: 'dev.corridor.05',
  epoch: 'dev.corridor.11',
  failed: 'dev.corridor.04',
};

export const DevCorridor: React.FC = () => {
  const { t } = useTranslation();
  const [state, setState] = useState<CorridorState>(getCorridorState);
  const conn = useKnoweStore(selectConn);
  const projectId = useKnoweStore(selectActiveProjectId);
  const sendMessage = useKnoweStore((s) => s.sendMessage);

  // 订阅走廊（不是轮询——record() 会主动推）
  useEffect(() => subscribeCorridor(setState), []);

  const counters: { key: keyof CorridorState; label: string }[] = [
    { key: 'zodRejected', label: t('dev.corridor.01') },
    { key: 'seqDropped', label: t('dev.corridor.02') },
    { key: 'outboundFailed', label: t('dev.corridor.04') },
    { key: 'sentinelAlerts', label: t('dev.corridor.05') },
    { key: 'epochResets', label: t('dev.corridor.11') },
  ];

  const download = (): void => {
    const blob = new Blob([exportCorridorJSON()], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `corridor-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="dc">
      <div className="dc-bar">
        <span className={'dc-conn dc-v-' + (conn === 'live' ? 'ok' : conn === 'reconnecting' ? 'warn' : 'info')}>
          {t('dev.corridor.connLabel', { conn })}
        </span>
        <span className="dc-hint">
          {t('dev.corridor.readonlyHint')}
        </span>
        <button className="btn btn-ghost" onClick={download}>{t('dev.corridor.06')}</button>
        <button className="btn btn-ghost" onClick={() => resetCorridor()}>{t('dev.corridor.10')}</button>
        <button
          className="btn btn-ghost"
          disabled={!projectId}
          onClick={() => { if (projectId) sendMessage(t('dev.corridor.12'), projectId); }}
        >
          {t('dev.corridor.sendProbe')}
        </button>
      </div>

      <div className="dc-counters">
        {counters.map(({ key, label }) => {
          const n = state[key] as number;
          return (
            <span key={key} className={'dc-counter' + (n > 0 ? ' hot' : '')}>
              {label} <b className="tnum">{n}</b>
            </span>
          );
        })}
      </div>

      <div className="dc-log">
        {state.entries.length === 0 ? (
          <div className="dc-empty">
            {t('dev.corridor.emptyHint')}
          </div>
        ) : (
          [...state.entries].reverse().map((e, i) => <Row key={state.entries.length - i} entry={e} />)
        )}
      </div>
    </div>
  );
};

const Row: React.FC<{ entry: CorridorEntry }> = ({ entry }) => {
  const { t } = useTranslation();
  return (
    <div className="dc-row">
      <span className="dc-ts tnum">{entry.ts.slice(11, 19)}</span>
      <span className="dc-dir">{entry.dir === 'in' ? '◀' : '▶'}</span>
      <span className={'dc-verdict dc-v-' + VERDICT_TONE[entry.verdict]}>
        {t(VERDICT_TEXT[entry.verdict])}
      </span>
      <span className="dc-type">{entry.type}</span>
      <span className="dc-meta tnum">
        {entry.projectId}
        {entry.seq >= 0 ? ` · seq ${entry.seq}` : ''}
      </span>
      <span className="dc-summary">{entry.summary}</span>
    </div>
  );
};

export default DevCorridor;
