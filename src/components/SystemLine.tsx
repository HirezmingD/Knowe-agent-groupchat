/**
 * SystemLine.tsx — 系统 / 编排提示行（component-tree §C）
 *
 * DOM：.sysline.enter(可 .tnum)「文字」
 *
 * level='error' 的系统消息（引擎级 error 事件）也走这条线，
 * 但要看得出是错的 —— 加 .err 修饰（样式见 knowe-components.css R5 区）。
 */

import React from 'react';

export interface SystemLineProps {
  text: string;
  level?: 'info' | 'error';
}

export const SystemLine: React.FC<SystemLineProps> = ({ text, level = 'info' }) => (
  <div
    className={'sysline enter' + (level === 'error' ? ' err' : '')}
    role={level === 'error' ? 'alert' : undefined}
  >
    {text}
  </div>
);

export default SystemLine;
