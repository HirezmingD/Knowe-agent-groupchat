/**
 * icons.tsx — 图标集（reference.html 的 ICON 常量 1:1 搬运）
 *
 * 每个图标的 path 直接抄自设计稿，不得自行改画。
 * IconAt 是唯一例外：它使用与输入框相同的文字字形，避免 SVG @ 与用户键入的 @
 * 在字高和基线上看起来像两个不同的符号。
 */

import React from 'react';

const S = {
  fill: 'none',
  stroke: 'currentColor',
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

export const IconLogo: React.FC = () => (
  <svg width="26" height="26" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M5 4v16" /><path d="M5 12l8-8" /><path d="M8.5 8.5L19 20" />
  </svg>
);

export const IconChats: React.FC = () => (
  <svg width="19" height="19" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

export const IconContacts: React.FC = () => (
  <svg width="19" height="19" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </svg>
);

export const IconFavorites: React.FC = () => (
  <svg width="19" height="19" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M12 3l2.7 5.6 6.1.8-4.5 4.3 1.1 6.1L12 16.9 6.6 19.8l1.1-6.1L3.2 9.4l6.1-.8z" />
  </svg>
);

export const IconKnowledge: React.FC = () => (
  <svg width="19" height="19" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H12v15H5.5A1.5 1.5 0 0 1 4 17.5z" />
    <path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H12v15h6.5a1.5 1.5 0 0 0 1.5-1.5z" />
  </svg>
);

export const IconSettings: React.FC = () => (
  <svg width="19" height="19" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M4 8h10M18 8h2M4 16h2M10 16h10" />
    <circle cx="16" cy="8" r="2.2" /><circle cx="8" cy="16" r="2.2" />
  </svg>
);

export const IconUsers: React.FC = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </svg>
);

export const IconTask: React.FC = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <rect x="5" y="4" width="14" height="17" rx="2.5" />
    <path d="M9 4.5V3.5A1.5 1.5 0 0 1 10.5 2h3A1.5 1.5 0 0 1 15 3.5v1" />
    <path d="m9 13 2 2 4-4.5" />
  </svg>
);

export const IconCheck: React.FC = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" {...S} strokeWidth="2">
    <path d="m4.5 12.5 5 5 10-11" />
  </svg>
);

export const IconX: React.FC = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);

export const IconRecover: React.FC = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M3 8a9 9 0 1 1 1 8" /><path d="M3 3v5h5" />
  </svg>
);

export const IconClip: React.FC = () => (
  <svg width="17" height="17" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="m21 12.5-8.5 8.5a5.66 5.66 0 0 1-8-8L13 4.5a3.77 3.77 0 0 1 5.33 5.33L9.9 18.26a1.89 1.89 0 0 1-2.67-2.67L15 7.85" />
  </svg>
);

export const IconAt: React.FC = () => (
  <span className="icon-at-glyph" aria-hidden="true">@</span>
);

export const IconUp: React.FC = () => (
  <svg width="17" height="17" viewBox="0 0 24 24" {...S} strokeWidth="1.8">
    <path d="M12 19V5" /><path d="m6 11 6-6 6 6" />
  </svg>
);

export const IconDown: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M12 5v14" /><path d="m18 13-6 6-6-6" />
  </svg>
);

export const IconChevR: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="m9 6 6 6-6 6" />
  </svg>
);

export const IconPlus: React.FC = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M12 5v14M5 12h14" />
  </svg>
);

export const IconSearch: React.FC = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <circle cx="11" cy="11" r="7" /><path d="m20 20-3.2-3.2" />
  </svg>
);

export const IconSearchSm: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <circle cx="11" cy="11" r="7" /><path d="m20 20-3.2-3.2" />
  </svg>
);

export const IconDots: React.FC = () => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor">
    <circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" />
  </svg>
);

export const IconAlert: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <circle cx="12" cy="12" r="9" /><path d="M12 7.5v5" />
    <circle cx="12" cy="16.2" r=".6" fill="currentColor" />
  </svg>
);

export const IconSpark: React.FC = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M12 3v4M12 17v4M5 12H1M23 12h-4M6.3 6.3 4 4M20 20l-2.3-2.3M6.3 17.7 4 20M20 4l-2.3 2.3" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

/** 空态大图标（.mark 内的三点环） */
export const IconEmptyMark: React.FC = () => (
  <svg width="88" height="88" viewBox="0 0 88 88" fill="none" stroke="currentColor" strokeWidth="1.5">
    <circle cx="44" cy="44" r="27" opacity="0.5" />
    <circle cx="44" cy="17" r="5.5" />
    <circle cx="67.4" cy="57.5" r="5.5" />
    <circle cx="20.6" cy="57.5" r="5.5" />
    <circle className="core" cx="44" cy="44" r="3.5" fill="currentColor" stroke="none" />
  </svg>
);

/** [v0.5 #11] 展开输入框 */
export const IconExpand: React.FC = () => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
    stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 9l4-4 4 4" />
    <path d="M8 15l4 4 4-4" />
  </svg>
);

/** [v0.5 #11] 收起输入框 */
export const IconCollapse: React.FC = () => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
    stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 5l4 4 4-4" />
    <path d="M8 19l4-4 4 4" />
  </svg>
);

/* ══════════════════════════════════════════════════════════════
   [v0.40.0] 右键菜单 + 收藏界面用到的图标 —— path 逐字抄自
   reference.html 的 Object.assign(ICON, {...})（2211–2248 行），不得自行改画。
   ══════════════════════════════════════════════════════════════ */

export const IconCopy: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <rect x="9" y="9" width="12" height="12" rx="2.5" />
    <path d="M5 15a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2" />
  </svg>
);

export const IconForward: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M13 5l7 7-7 7" />
    <path d="M20 12H5a1 1 0 0 0-1 1v4" />
  </svg>
);

/** 五角星（收藏）。Rail 的 IconFavorites 是 19×19 的导航尺寸；菜单里用 16×16 这颗。 */
export const IconStar: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M12 3l2.7 5.6 6.1.8-4.5 4.3 1.1 6.1L12 16.9 6.6 19.8l1.1-6.1L3.2 9.4l6.1-.8z" />
  </svg>
);

export const IconCheckbox: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <rect x="4" y="4" width="16" height="16" rx="3" />
    <path d="m8.5 12 2.5 2.5 4.5-5" />
  </svg>
);

export const IconQuote: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M8 7H5a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h2v3H4M18 7h-3a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h2v3h-3" />
  </svg>
);

export const IconTrash: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M4 7h16M9 7V5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 5v2M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
  </svg>
);

export const IconLink: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M9 15l6-6M8.5 7.5 10 6a3.5 3.5 0 0 1 5 5l-1.5 1.5M15.5 16.5 14 18a3.5 3.5 0 0 1-5-5l1.5-1.5" />
  </svg>
);

export const IconImage: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <rect x="3" y="4" width="18" height="16" rx="2.5" />
    <circle cx="8.5" cy="9.5" r="1.6" />
    <path d="m4 18 5-5 4 3 3-3 4 4" />
  </svg>
);

export const IconEdit: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M12 20h9" />
    <path d="M16.5 3.5a2 2 0 0 1 3 3L7 19l-4 1 1-4z" />
  </svg>
);

export const IconFolder: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
);

export const IconBook: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H12v15H5.5A1.5 1.5 0 0 1 4 17.5z" />
    <path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H12v15h6.5a1.5 1.5 0 0 0 1.5-1.5z" />
  </svg>
);

export const IconReport: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
    <path d="M14 3v4h4M8.5 13h7M8.5 16.5h5" />
  </svg>
);

export const IconTag: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M20 12l-8 8-8-8V4h8z" />
    <circle cx="8.5" cy="8.5" r="1.2" />
  </svg>
);

/* ══════════════════════════════════════════════════════════════
   [v1.0.19.2] 深浅色切换图标 —— path 逐字抄自 Knowe-UI_v1.3.html
   L1328–L1329（#iconMoon / #iconSun），不得自行改画。
   浅色模式显示月牙（可切到深色），深色模式显示太阳（可切回浅色）。
   ══════════════════════════════════════════════════════════════ */

export const IconMoon: React.FC = () => (
  <svg width="19" height="19" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" />
  </svg>
);

export const IconSun: React.FC = () => (
  <svg width="19" height="19" viewBox="0 0 24 24" {...S} strokeWidth="1.5">
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
);
