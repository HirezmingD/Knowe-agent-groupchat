/**
 * sessionActiveContext.ts — [v1.0.24.6-P0] 会话活动态 Context
 *
 * ChatStream 包 Provider（value = active prop），子组件（MessageBubble/ReasoningPanel/
 * ApprovalCard/DirectoryRecoveryCard）消费——隐藏会话（active=false）停掉自己的
 * RO/rAF/setInterval 等循环（渲染次数病根治，见 06-性能基线）。
 *
 * 独立成文件：避免 ChatStream → MessageBubble → ChatStream 的循环 import。
 * 默认 true：非 ChatStream 树内渲染（测试/独立使用）不受影响。
 */
import React from 'react';

export const SessionActiveContext = React.createContext<boolean>(true);

/** 便捷 hook：当前会话是否活动（隐藏会话 = false）。 */
export function useSessionActive(): boolean {
  return React.useContext(SessionActiveContext);
}
