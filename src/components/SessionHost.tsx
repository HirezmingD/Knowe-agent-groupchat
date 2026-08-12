/**
 * SessionHost.tsx — [v1.0.23.5] 会话视图常驻内存 · 宿主
 *
 * 架构：每个打开过的会话拥有一个独立的 ChatStream 实例（独立滚动容器/行高/窗口/滚动位置），
 *       全部常驻挂载；切换 = 只改 .session 的 active class（opacity 合成器切换，零重绘）。
 *       —— 微信/QQ 式「每会话视图常驻内存」，消除切群「恢复动作」及其跳变。
 *
 * 机制：
 *   · 懒创建：activeProjectId 第一次出现才创建实例；创建后常驻不销毁；
 *   · openedRef 是 ref 不是 state → 新会话入列不触发宿主重渲染（唯一重渲染源是 activeId）；
 *   · 会话从 store 消失（归档/删除）→ 对应实例销毁（下次 activeId 变化时自然生效）；
 *   · 显隐：非活动实例 opacity:0 + pointer-events:none（布局/scrollTop/行高全保留）。
 *
 * [v1.0.24.6-P0] 活动态下传：active prop 传给 ChatStream——非活动会话「停摆」：
 *   停 ResizeObserver/rAF/入场动画/倒计时/贴底跟随（渲染次数病根治，见 06-性能基线）。
 *   只停循环不停挂载：切回时行高缓存/滚动位置原样恢复（active 变 true 即唤醒）。
 */

import React, { useEffect, useRef } from 'react';
import { useKnoweStore } from '../store/store';
import { selectActiveProjectId } from '../store/selectors';
import ChatStream, { type ChatSearchJump } from './ChatStream';

/** 每会话常驻实例的包装层（供 CSS 定位 + 显隐切换）。 */
export interface SessionHostProps {
  rosterOpen: boolean;
  onToggleRoster: () => void;
  searchJump?: ChatSearchJump | null;
  onSearchJumpDone?: (requestId: number) => void;
}

export const SessionHost: React.FC<SessionHostProps> = ({
  rosterOpen,
  onToggleRoster,
  searchJump = null,
  onSearchJumpDone,
}) => {
  const activeId = useKnoweStore(selectActiveProjectId);

  // 打开过的会话集合 = 常驻实例集合（ref，不入 state）
  const openedRef = useRef<Set<string>>(new Set());
  if (activeId) openedRef.current.add(activeId);

  // 会话从 store 消失（归档/删除）→ 从常驻集合移除，下次渲染自然销毁实例
  useEffect(() => {
    const alive = new Set(Object.keys(useKnoweStore.getState().convs));
    for (const pid of openedRef.current) {
      if (!alive.has(pid)) openedRef.current.delete(pid);
    }
  });

  const pids = [...openedRef.current];
  return (
    <div className="sessions">
      {pids.map((pid) => (
        <div
          key={pid}
          className={'session' + (pid === activeId ? ' active' : '')}
          aria-hidden={pid !== activeId || undefined}
        >
          <ChatStream
            projectId={pid}
            active={pid === activeId}
            rosterOpen={rosterOpen}
            onToggleRoster={onToggleRoster}
            searchJump={searchJump}
            onSearchJumpDone={onSearchJumpDone}
          />
        </div>
      ))}
    </div>
  );
};

export default SessionHost;
