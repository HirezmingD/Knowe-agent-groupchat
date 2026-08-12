/**
 * StreamBubble.tsx — 可观察工作阶段气泡。
 *
 * [v1.0.23.3] 重写：删除八阶段聚合（stageLabel/stagePhrase/toolTechnicalDetail），
 * 回归「推理面板 + typing」。流式期间实时展示 LLM 推理（reasoning_content 透传），
 * 正文仍只在 message 落定后一次显示。
 *
 * [v1.0.23.3 修订] 流式期间 typing-bubble 的内容 = LLM 推理过程本身：
 *   有推理 → 只渲染推理面板（live），不再显示「正在输入中」文案；
 *   无推理（模型不输出 reasoning_content）→ 三点动画兜底，同样无文案。
 */

import React, { useEffect } from 'react';
import { Avatar } from './Avatar';
import { senderLineOf, type AgentFace } from './MessageBubble';
import { ReasoningPanel, ThinkingDot } from './ReasoningPanel';

export interface StreamBubbleProps {
  /** 已收到的增量正文；本组件故意不渲染，仅供 message 空 content 时兜底。 */
  text?: string;
  /** [v1.0.23.3] 流式累积中的推理文本（reasoning_delta 实时追加）。 */
  reasoning?: string;
  /** live-only 首帧保护 id。 */
  frameId?: string;
  /** 过程态完成首帧后回执给状态层。 */
  onFramePaint?: (frameId: string) => void;
  /** 权威终态已到但首帧尚未确认；此帧只展示通用输入反馈。 */
  settling?: boolean;
  grouped?: boolean;
  face?: AgentFace;
}

export const StreamBubble: React.FC<StreamBubbleProps> = ({
  reasoning,
  frameId,
  onFramePaint,
  grouped = false,
  face,
}) => {
  const f: AgentFace = face ?? { name: 'Agent', role: 'Agent', glyph: '?', pal: 'av-d' };
  // [v1.0.23.3 修订] 不再因 settling 抑制推理：推理增量一到就显示（用户要求推理先行），
  //   settling（权威终态已到、首帧未确认）期间推理面板保持，不切回三点。
  const hasReasoning = !!reasoning;

  useEffect(() => {
    if (!frameId || !onFramePaint) return undefined;
    let cancelled = false;
    let raf = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const acknowledge = (): void => {
      if (!cancelled) onFramePaint(frameId);
    };

    // passive effect 已在首帧提交后运行；再跨一个 RAF，确保浏览器真正有机会绘制临时态。
    if (typeof globalThis.requestAnimationFrame === 'function') {
      raf = globalThis.requestAnimationFrame(acknowledge);
    } else {
      timer = setTimeout(acknowledge, 0);
    }
    return () => {
      cancelled = true;
      if (raf && typeof globalThis.cancelAnimationFrame === 'function') {
        globalThis.cancelAnimationFrame(raf);
      }
      if (timer) clearTimeout(timer);
    };
  }, [frameId, onFramePaint]);

  return (
    <div
      className={'mgroup enter' + (grouped ? ' same' : '')}
      data-streaming="1"
      data-transient-frame={frameId}
    >
      {!grouped && <div className="sender-line">{senderLineOf(f)}</div>}
      <div className={'mrow' + (grouped ? ' cont' : '')}>
        <div className="sel-box" />
        <div className="m-av">
          {!grouped && (
            <Avatar glyph={f.glyph} pal={f.pal} size={36} title={f.name} src={f.avatarUrl} />
          )}
        </div>

        <div
          className={'bubble agent typing-bubble' + (hasReasoning ? ' has-reasoning' : '')}
          aria-live="polite"
          aria-busy="true"
        >
          {/* [v1.0.23.3] 流式期间 typing-bubble 显示 LLM 推理过程（有推理才渲染，占满气泡） */}
          {hasReasoning ? (
            <ReasoningPanel text={reasoning || ''} live />
          ) : (
            /* [v1.0.24.4] 无推理（模型不输出 reasoning_content）→ morphing-infinity
               指示器（thinking-dot）兜底——不再用三点动画 */
            <ThinkingDot />
          )}
        </div>

        <div className="m-time" />
      </div>
    </div>
  );
};

export default StreamBubble;
