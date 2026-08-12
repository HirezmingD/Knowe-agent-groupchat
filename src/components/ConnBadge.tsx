/**
 * ConnBadge.tsx — 连接状态徽章（六态）
 *
 * ⚠ 设计稿没有这个组件（reference.html 是纯前端 demo，不存在真实连接状态）。
 *   但和洲不是开发者，「连没连上」必须在屏幕上看得见，不能靠 Console。
 *   这是全套 UI 中唯一一处超出设计稿的新增件，样式只用既有设计令牌，
 *   放在标题栏右侧 .title-right（与 .tbtn 同排），不侵入任何设计稿区域。
 *
 * 六态 → 颜色（按需求）：live=绿，reconnecting=黄，其余=灰。
 */

import React from 'react';
import { useKnoweStore } from '../store/store';
import { selectConn } from '../store/selectors';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';

const TEXT: Record<string, string> = {
  connecting: 'conn.badge.04',
  handshaking: 'conn.badge.02',
  live: 'conn.badge.01',
  resync: 'conn.badge.05',
  reconnecting: 'conn.badge.06',
  closed: 'conn.badge.03',
};

/** live=绿(ok) / reconnecting=黄(warn) / 其余=灰(默认) */
const TONE: Record<string, string> = {
  live: ' ok',
  reconnecting: ' warn',
};

export const ConnBadge: React.FC = () => {
  const { t } = useTranslation();
  const conn = useKnoweStore(selectConn);
  // [阶段一 1.5] 正式版（打包安装版）零开发痕迹：连接徽章是开发调试件，不渲染。
  //   注意：hooks 全部在 return 之前调用，满足 hooks 规则；App.tsx 侧另有同款分支，双保险。
  if (window.knowe?.isPackaged === true) return null;
  const tone = TONE[conn] ?? '';

  return (
    <div
      className={'conn-badge' + tone}
      role="status"
      aria-live="polite"
      data-conn={conn}
      title={i18n.t('conn.badge.07', { state: TEXT[conn] ? t(TEXT[conn]) : conn })}
    >
      <span className="cb-dot" />
      <span className="cb-tx">{TEXT[conn] ? t(TEXT[conn]) : conn}</span>
    </div>
  );
};

export default ConnBadge;
