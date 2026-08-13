/**
 * DirectoryRecoveryCard.tsx — 目录恢复卡片（[v0.13 卡片]）
 *
 * 取代原来的「服务器级目录失效 → Electron 原生目录弹窗」。它渲染在**聊天消息流里、群聊头部
 * 下方**——和知知的建群卡、项目经理的拉人卡/派活卡同一个位置（由 ChatStream 挂在 .msgs 末尾），
 * 而不是钉在聊天区顶部（那会跑到群名上方，很怪）。风格照搬 ApprovalCard（复用 .approval / .ap-*
 * 那套 CSS），交互照搬 NewProjectModal（项目名 + DirectoryPicker，可同时改名和重选目录）。
 *
 * 三种呈现（由 store 的 openUntil 决定，见 directoryRecovery.ts 的状态机）：
 *   · 该项目目录正常                    → 不渲染（null）
 *   · 有待处理、且在 5 分钟展开窗口内    → 完整卡片（倒计时 + 项目名 + 目录 + 确认/拒绝）
 *   · 有待处理、但已收起（超时/被取消）  → 一条红色「未处理事项」细条，点它重开卡片
 *
 * 需求对应：
 *   1 聊天区内卡片（非原生弹窗）  2 五分钟倒计时、超时收起  4 可改名+可重选  6 可取消+可重开
 *
 * 铁律：
 *   · 倒计时归零 = 用户点「拒绝」：都走 store.cancel（回传 cancel_project_directory + 收起），
 *     绝不自己在前端把项目「恢复」——恢复只认后端的 project_directory_restored。
 *   · 目录必选；项目名预填当前名，改了才回传（后端按同名跳过改名）。
 */

import React, { useEffect, useRef, useState } from 'react';
import { useSessionActive } from './sessionActiveContext';
import DirectoryPicker from './DirectoryPicker';
import {
  useDirectoryEntry,
  reopen,
  cancel,
  confirm,
  DIRECTORY_CARD_DURATION_MS,
  type DirectoryEntry,
} from '../store/directoryRecovery';
import { useTranslation } from 'react-i18next';

/** 秒 → M:SS */
function fmt(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

const FolderIcon: React.FC = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
);

// ── 自注入样式：只覆盖 .approval 之外的新元素（置顶壳 / 收起红条）。
//    卡片主体本身用现成的 .approval / .ap-* 类，跟着主题走。 ──
const STYLE_ID = 'knowe-dir-recovery-styles';
function ensureStyles(): void {
  if (typeof document === 'undefined') return;
  if (document.getElementById(STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = STYLE_ID;
  el.textContent = `
/* [位置修正] 卡片现在渲染在消息流里（不再套「置顶壳」）。完整卡片直接复用 .approval
   的定位/间距，和审批卡长一样；这里只给收起态那条红色细条一点上下留白，让它像列表里的一项。 */
.dir-recovery-bar{
  display:flex; align-items:center; gap:8px; width:100%; margin:6px 0;
  padding:9px 13px; border:1px solid rgba(229,72,77,.35);
  background:rgba(229,72,77,.08); color:#c0392b;
  border-radius:10px; font-size:13px; font-weight:600; cursor:pointer;
  text-align:left; transition:background .15s ease;
}
.dir-recovery-bar:hover{ background:rgba(229,72,77,.14); }
.dir-recovery-dot{ width:8px; height:8px; border-radius:50%; background:#e5484d; flex:0 0 auto; }
.dir-recovery-bar .grow{ flex:1 1 auto; }
.dir-recovery-bar .go{ opacity:.7; font-weight:500; }
.dir-recovery-prev{ opacity:.72; }
`;
  document.head.appendChild(el);
}

// ═══════════════════════════════════════════════════════════════

export interface DirectoryRecoveryCardProps {
  /** 当前会话 id（卡片只为「正在看的这个项目」渲染）。 */
  projectId: string | null;
}

export const DirectoryRecoveryCard: React.FC<DirectoryRecoveryCardProps> = ({ projectId }) => {
  const entry = useDirectoryEntry(projectId);
  useEffect(() => { ensureStyles(); }, []);

  if (!projectId || !entry) return null;

  const open = entry.openUntil > Date.now();
  // 不再套「置顶壳」：直接返回卡片本体。完整卡片的 .approval 根节点作为 .msgs 的直接子节点，
  // 和审批卡受同一套 CSS 摆布，位置/宽度天然一致。
  // key：换一次「打开」（重开 / 目录非法后端重发）就重挂一张干净的卡，重置输入与提交态。
  return open
    ? <FullCard key={entry.request.requestId + ':' + entry.openUntil} projectId={projectId} entry={entry} />
    : <CollapsedBar projectId={projectId} />;
};

// ── 收起态：顶部一条红色「未处理事项」，点它重开（需求 6 的入口） ──
const CollapsedBar: React.FC<{ projectId: string }> = ({ projectId }) => {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      className="dir-recovery-bar"
      onClick={() => reopen(projectId)}
      aria-label={t('directory.recovery.card.04')}
    >
      <span className="dir-recovery-dot" />
      <span className="grow">{t('directory.recovery.card.02')}</span>
      <span className="go">{t('directory.recovery.card.03')}</span>
    </button>
  );
};

// ── 展开态：完整卡片 ──
const FullCard: React.FC<{ projectId: string; entry: DirectoryEntry }> = ({ projectId, entry }) => {
  const { t } = useTranslation();
  const { request, openUntil } = entry;
  const [name, setName] = useState(request.projectName);
  const [dir, setDir] = useState('');
  const [attempted, setAttempted] = useState(false);   // 按过确认才有资格标红（同 NewProjectModal）
  const [submitting, setSubmitting] = useState(false);  // 点过确认 → 立即禁用防抖，等后端回话
  const [remain, setRemain] = useState<number>(() => Math.max(0, (openUntil - Date.now()) / 1000));
  const barRef = useRef<HTMLDivElement>(null);
  const firedRef = useRef(false);
  const active = useSessionActive();

  // ── 客户端 5 分钟倒计时（本地起算，允许重开时重新计时）──
  // [v1.0.24.6-P0] 隐藏会话停摆：不跑倒计时（恢复时 active 变 true → effect 重跑，超时取消照常触发）
  useEffect(() => {
    if (!active) return;
    const total = DIRECTORY_CARD_DURATION_MS / 1000;
    const tick = (): void => {
      const left = (openUntil - Date.now()) / 1000;
      setRemain(left);
      const bar = barRef.current;
      if (bar) bar.style.transform = `scaleX(${Math.max(0, Math.min(100, (left / total) * 100)) / 100})`;
      if (left <= 0 && !firedRef.current) {
        firedRef.current = true;
        clearInterval(timer);
        // 超时 = 回退到暂缓态：回传取消并收起（红字留着，不循环弹）。
        cancel(projectId);
      }
    };
    const timer = setInterval(tick, 1000);
    tick();
    return () => clearInterval(timer);
  }, [projectId, openUntil, active]);

  const timedOut = remain <= 0;
  const nameBad = attempted && !name.trim();
  const dirBad = attempted && !dir.trim();

  const doConfirm = (): void => {
    const n = name.trim();
    const d = dir.trim();
    if (!n || !d) { setAttempted(true); return; }   // 缺什么按下去精确标红，不用灰键
    setSubmitting(true);
    // 名字改了才回传；没改传 undefined，后端按同名跳过改名。
    confirm(projectId, d, n !== request.projectName ? n : undefined);
  };

  return (
    <div className="approval enter-soft" data-dir-recovery={projectId}>
      <div className="ap-head">
        <FolderIcon />
        <span className="ap-label">{t('directory.recovery.card.04')}</span>
        <span className="ap-count">{timedOut ? t('approval.card.03') : t('approval.card.01') + ' ' + fmt(remain)}</span>
      </div>

      <div className="ap-note">
        {t('directory.recovery.card.body1')}
        {t('directory.recovery.card.body2')}
        {request.previousDir
          ? <span className="dir-recovery-prev">{t('directory.recovery.card.prevPath', { path: request.previousDir })}</span>
          : null}
      </div>

      <div className="ap-project">
        <label className="ap-project-label" htmlFor={`dr-name-${projectId}`}>{t('common.23')}</label>
        <input
          id={`dr-name-${projectId}`}
          className={'ap-project-input' + (nameBad ? ' needs-pick' : '')}
          type="text"
          value={name}
          disabled={submitting || timedOut}
          maxLength={40}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setName(e.target.value)}
          onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
            if (e.key === 'Enter' && !submitting && !timedOut) doConfirm();
          }}
        />
        {nameBad && <div className="dir-hint" role="alert">{t('approval.card.04')}</div>}
      </div>

      <div className="ap-project">
        <label className="ap-project-label">{t('common.24')}</label>
        <DirectoryPicker
          value={dir}
          onChange={setDir}
          label={t('common.04')}
          required
          showError={dirBad}
        />
      </div>

      <div className="ap-actions">
        <button
          className="btn btn-primary"
          disabled={submitting || timedOut}
          onClick={doConfirm}
        >
          {submitting ? t('directory.recovery.card.01') : t('common.20')}
        </button>
        <button
          className="btn btn-ghost"
          disabled={submitting}
          onClick={() => cancel(projectId)}
        >
          {t('approval.card.13')}
        </button>
      </div>

      <div className="ap-progress" ref={barRef} />
    </div>
  );
};

export default DirectoryRecoveryCard;
