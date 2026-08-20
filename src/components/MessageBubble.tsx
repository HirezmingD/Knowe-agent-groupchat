/**
 * MessageBubble.tsx — 消息组 / 气泡（component-tree §C · MessageBubble）
 *
 * 用户：.mgroup > (.mrow.me > .sel-box + .m-time [+ .m-sending] [+ .m-fail] + .bubble.me(.tail))
 * Agent：.mgroup(.same) > [.sender-line] + (.mrow(.cont) > .sel-box + .m-av + .bubble.agent(.tail) + .m-time)
 *
 * 分组规则（照设计稿 sameSender）：
 *   grouped = 与上一条同一发送者 → .mgroup.same，且不再重复头像与名字行
 *   tail    = 与下一条不同发送者 → .bubble.tail（气泡尖角只在一组的最后一个）
 *
 * 乐观渲染三态（UserItem.delivery）：
 *   pending   → .m-sending（转圈点）
 *   confirmed → 无标记（服务端已回声）
 *   suspect   → .m-fail（5s 无回声，屏幕上必须看得见）
 *
 * [v0.8d #3] 长消息折叠：见 useFoldable()。
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useCachedT } from '../i18n';
import { assistantRoleLabel } from '../shared/roleLabel';
import { Avatar } from './Avatar';
import { Markdown } from './markdown';
import { IconAlert, IconCheck, IconForward } from './icons';
import { linkifyBareUrls, linkifyLineToNodes } from '../utils/links';
import type { ProducedFile, AttachmentInput, ForwardMeta, SuggestionItem } from '../store/state';
import { FileCardList, AttachmentCardList } from './FileCard';
import { ReasoningPanel, ThinkingDot } from './ReasoningPanel';
import { Suggestions } from './Suggestions';
import { useSessionActive } from './sessionActiveContext';

export interface AgentFace {
  name: string;
  role: string;
  glyph: string;
  pal: string;
  /** [v0.4] 头像图片（知知是 zinnia.png，其余从池子里按 id 派生） */
  avatarUrl?: string;
}

/**
 * [v0.7 #3] 发送者行怎么写——**一处判定**（StreamBubble 也 import 这一个）。
 *
 *   项目经理的名字本来就是「官网改版 · 项目经理」（avatar.ts 的 faceFor 给的），
 *   后面再拼一个 role「项目经理」，屏幕上就成了「官网改版 · 项目经理 · 项目经理」——
 *   同一个词说两遍，像结巴。
 *
 *   规矩：角色已经在名字里出现过了，就不再单独说一遍。
 *   （普通成员不受影响：「Ada · 前端」里的「前端」不在「Ada」里，照常拼。）
 */
export function senderLineOf(face: AgentFace): string {
  const role = (face.role ?? '').trim();
  if (!role || face.name.includes(role)) return face.name;
  // [v1.0.38.2] 助手化称呼 + 全角括号格式：林知远（界面设计助手）
  return `${face.name}（${assistantRoleLabel(role)}）`;
}

// ═══════════════════════════════════════════════════════════════
// [v0.8d #3] 长消息折叠
// ═══════════════════════════════════════════════════════════════

/*
 * 折叠后露出多高 → **在 CSS 里**（.bubble.folded .bubble-body { max-height: 184px }）。
 * ≈7 行正文（line-height 24px × 7 + 一点余量）。
 * 这里不写死数字，是因为铁律 1 禁止 JSX 的 style={{}}：高度归样式表管。
 *
 * [v1.0.24.4] 展开/收起的 morph 过渡需要 JS 给精确的起始/目标高度
 * （max-height 从 184px 到「内容高度」逐像素过渡，CSS 无法表达"内容多高"），
 * 因此 toggle 里用 ref 直改 style（铁律 1 允许的 imperative 通道，同 CountdownBar）。
 * 这个数字与 CSS 的 .bubble.folded .bubble-body { max-height } **必须保持一致**。
 */
const FOLD_MAX_H = 184;

/**
 * 多高才算「长」。
 *
 * 不写死一个数：4K 屏上 500px 只是半屏，笔记本上就是一整屏。
 * 「超出这扇窗能一眼看下的范围」才叫长 —— 所以拿窗口高度来量。
 * 下限 360px：窗口再矮，也不该为了六行字就把人家折起来。
 */
function foldThreshold(): number {
  if (typeof window === 'undefined') return 480;
  return Math.max(360, Math.round(window.innerHeight * 0.55));
}

/**
 * 这块内容要不要折。
 *
 * ★ 量的是内容的 **scrollHeight**，不是气泡的 clientHeight —— 折起来之后气泡矮了，
 *   可内容还是那么长（overflow:hidden 不改 scrollHeight）。拿 clientHeight 去量，
 *   会得到「折起来 → 不长了 → 展开 → 又长了」的抖动。
 *
 * ResizeObserver 是必须的：Markdown 里的表格、KaTeX 公式、代码高亮都是
 * **渲染完才知道多高**的，第一帧量到的高度是骗人的。
 */
function useFoldable(text: string): {
  bodyRef: React.RefObject<HTMLDivElement>;
  foldable: boolean;
  folded: boolean;
  toggle: () => void;
} {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [foldable, setFoldable] = useState(false);
  const [folded, setFolded] = useState(true);      // 一旦判定为长 → 默认折着

  const measure = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    // [v1.0.23.4] 值未变不 setState：ResizeObserver 高频回调时避免无谓重渲染
    const next = el.scrollHeight > foldThreshold();
    setFoldable((prev) => (prev === next ? prev : next));
  }, []);

  // [v1.0.23.4] 测量被动化（架构设计 §3.10）：mount 不同步读 scrollHeight（强制 reflow），
  //   改为渲染后一帧再测——切群时 N 条气泡不再同步 reflow + 同步 setState 链。
  //   RO 保留：内容高度变化（表格/代码/图片渲染完成）仍要重新判定。
  // [v1.0.24.6-P0] 隐藏会话停摆：不建 RO（active=false，恢复时 effect 重跑自然重建）
  const active = useSessionActive();
  useEffect(() => {
    if (!active) return;
    let raf = 0;
    raf = requestAnimationFrame(() => measure());
    const el = bodyRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(el);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, [measure, text, active]);

  // 窗口高度变了 →「长」的标准跟着变
  // [v1.0.23.13] ★ rAF 节流：每条气泡都挂 resize 监听，拖窗口时几十条同时触发
  //   measure()（读 scrollHeight = 强制 reflow）就是「拖窗口卡」的根源。
  //   用 rAF 把同一帧内的多次 resize 合并成一次测量。
  // [v1.0.24.6-P0] 隐藏会话停摆：不挂 resize 监听（active=false）
  useEffect(() => {
    if (!active) return;
    let raf = 0;
    const onResize = (): void => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        measure();
      });
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [measure, active]);

  // [v1.0.24.4] ★ 展开/收起 morph：JS 精确 max-height 过渡（慢→快→慢）。
  //   折叠 184px ↔ 内容高度之间逐像素 morph，速率曲线 cubic-bezier(0.77,0,0.175,1)
  //   （强 ease-in-out：慢起→快中段→慢收尾，用户点名「慢→快→慢」）。
  //   [v1.0.24.4-r2] 时长 2s → 1s（用户实测「2秒太慢」）。
  //   [v1.0.24.4-r3] 1s → 0.8s（用户再次实测微调）。
  //   原理：先钉住当前高度 → 强制 reflow 让起始值生效 → 再过渡到目标高度；
  //   transitionend 后清除 inline（回 CSS 类控制：折叠 184px / 展开无上限）。
  //   快速连点天然可中断（transition 从当前值接管）。
  const morphRef = useRef<((ev: TransitionEvent) => void) | null>(null);
  const toggle = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    const targetFolded = !folded;
    const startH = targetFolded ? el.scrollHeight : FOLD_MAX_H;
    const endH = targetFolded ? FOLD_MAX_H : el.scrollHeight;
    if (morphRef.current) {
      el.removeEventListener('transitionend', morphRef.current);
      morphRef.current = null;
    }
    el.style.transition = 'none';
    el.style.maxHeight = `${startH}px`;
    void el.offsetHeight;          // 强制 reflow：让起始高度先落地，下一帧才过渡
    el.style.transition = 'max-height 800ms cubic-bezier(0.77, 0, 0.175, 1)';
    el.style.maxHeight = `${endH}px`;
    const done = (ev: TransitionEvent): void => {
      if (ev.target !== el || ev.propertyName !== 'max-height') return;
      el.style.transition = '';
      el.style.maxHeight = '';
      morphRef.current = null;
    };
    morphRef.current = done;
    el.addEventListener('transitionend', done);
    setFolded(targetFolded);
  }, [folded]);
  return { bodyRef, foldable, folded: foldable && folded, toggle };
}

/**
 * 气泡的壳：内容 + （必要时）底部的渐变遮罩和那一行小字。
 *
 * 遮罩和按钮都刻意做得很轻：一条从透明到气泡底色的渐变、一行 12px 的灰字。
 * 它们是**路标**，不是广告——不该比消息本身更抢眼。
 *
 * [v1.0.23.3] before/after 插槽：推理面板与四方向卡片与正文同处一个气泡
 * （照 apple 参考 .assistant-bubble = reasoning + answer + suggestions 三段）。
 * 折叠只作用于 .bubble-body（正文）；推理面板、卡片不受折叠影响。
 */
const FoldableBubble: React.FC<{
  className: string;
  text: string;
  children: React.ReactNode;
  /** [v1.0.23.3] 气泡内第一段（推理面板，渲染在正文前）。 */
  before?: React.ReactNode;
  /** [v1.0.23.3] 气泡内第三段（四方向卡片，渲染在正文后、折叠把手前）。 */
  after?: React.ReactNode;
  /** [v1.0.23.4] 流式期间：同一壳（bubble agent tail）承载三点 → 推理 → 正文，全程 morph 不换壳。 */
  streaming?: boolean;
  /** [v1.0.23.4] 流式累积推理文本（有值 → 推理面板 live；无 → 三点动画）。 */
  streamingReasoning?: string;
}> = ({ className, text, children, before, after, streaming = false, streamingReasoning }) => {
  const { t } = useCachedT();
  const { bodyRef, foldable, folded, toggle } = useFoldable(text);

  return (
    <div
      className={
        className
        + (foldable ? ' foldable' : '') + (folded ? ' folded' : '')
        /* [v1.0.23.4] 统一壳：流式期间也是 bubble agent tail（不再切 typing-bubble） */
        + (streaming ? ' streaming' : '')
        + (streaming && streamingReasoning ? ' has-reasoning' : '')
      }
    >
      {/* [v1.0.23.3] 模块一：推理折叠面板（照参考位于气泡内顶部；正文有 border-top 分隔）
          [v1.0.23.4] 流式期间同壳：有推理 → 推理面板 live；无推理 → 三点动画 */}
      {streaming ? (
        streamingReasoning ? (
          <ReasoningPanel text={streamingReasoning} live />
        ) : (
          /* [v1.0.24.4] 推理未到达 → morphing-infinity 指示器（thinking-dot）兜底，不再用三点 */
          <ThinkingDot />
        )
      ) : before}
      {/* [v1.0.23.4] 模块二：正文（wrap 恒渲染，0fr↔1fr grid 过渡 = 向下 morph 展开，
          无跳闪；折叠高度写在 CSS 里（.bubble.folded .bubble-body）——不用 JSX style。 */}
      <div className={'bubble-body-wrap' + (streaming || !children ? ' is-empty' : '')}>
        <div className="bubble-body" ref={bodyRef}>
          {children}
        </div>
      </div>
      {/* [v1.0.23.5] 展开/收起按钮：管的是正文，贴正文下沿、四方按钮上方 */}
      {foldable && (
        <button className="bubble-more" onClick={toggle} aria-expanded={!folded}>
          {folded ? t('message.bubble.03') : t('file.card.01')}
        </button>
      )}

      {/* [v1.0.23.3] 模块三：四方向建议卡片（wrap 恒渲染，到达时 0fr→1fr 展开） */}
      <div className={'bubble-after-wrap' + (after ? '' : ' is-empty')}>
        {after}
      </div>

      {/*
        [v0.8e #5] ★ 展开之后，「收起」得**跟着你走**。

          展开一段两屏长的回复，那颗「收起」在气泡最底下——想收回去，
          得先滚过整段你正嫌它长的东西。这不是不方便，这是荒谬。

          所以：展开时，气泡右边挂一条和气泡一样高的轨（.bubble-rail），
          轨里那颗按钮是 position: sticky —— 你滚到哪儿，它就停在视口中线附近，
          伸手就够得着。气泡滚出屏幕，它自然也跟着走，不会赖在那儿。

          样子做得很轻：一颗小圆钮，一个向上的箭头，悬停才显出「收起」两个字。
          它是个把手，不是个按钮墙。
      */}
      {foldable && !folded && (
        <div className="bubble-rail" aria-hidden="false">
          <button
            className="bubble-collapse"
            onClick={toggle}
            title={t('file.card.01')}
            aria-label={t('message.bubble.04')}
          >
            <IconChevronUp />
            <span className="lbl">{t('file.card.01')}</span>
          </button>
        </div>
      )}
    </div>
  );
};

/** 向上的小箭头。图标库里没有现成的，就地画一个——12×12，跟按钮一样低调。 */
const IconChevronUp: React.FC = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path
      d="M6 15l6-6 6 6"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

// ═══════════════════════════════════════════════════════════════

export interface MessageBubbleProps {
  kind: 'user' | 'agent';
  text: string;
  /** 与上一条同发送者 */
  grouped?: boolean;
  /** 与下一条不同发送者（气泡带尖角） */
  tail?: boolean;
  /** 用户消息的送达状态 */
  delivery?: 'pending' | 'confirmed' | 'suspect';
  /** Agent 消息的身份 */
  face?: AgentFace;
  /** [v0.37] 这条 agent 气泡是谁说的——双击进私聊要用它。 */
  agentId?: string;
  /**
   * [v0.37] 双击发送者行/头像 → 进入与该 agent 的私聊。
   *   只在**群聊**里由 ChatStream 传入；私聊/知知窗口里不传 → 不可双击（没有「私聊里再私聊」）。
   */
  onOpenDm?: (agentId: string) => void;
  /** [v0.44.9] Agent 头像右键菜单；头像事件必须截断，不能冒泡到 .mgroup 的消息菜单。 */
  onAgentContextMenu?: (e: React.MouseEvent, agentId: string) => void;
  /** [v0.36] 本轮 Worker 产出的文件——渲染在气泡正下方，点开走预览面板。 */
  files?: ProducedFile[];
  /** [v0.36] 当前会话的 project_id，文件卡片取文件时要用它拼 /preview 地址。 */
  projectId?: string;
  /** [v0.38.3 #3] 这条消息的事件 seq——标在 .mgroup 的 data-seq 上，供「跳转到消息出处」定位。 */
  domSeq?: number;

  // ── [v0.40.0] 右键菜单 / 多选 / 翻译（全部由 ChatStream 注入；不传 = 行为与从前一致） ──

  /** 这条消息在本会话视图里的稳定钥匙（state.itemKeyOf）。多选/翻译/删除都认它。 */
  itemKey?: string;
  /** 右键 → ChatStream 打开消息菜单（气泡、文件卡、图片、视频都从 .mgroup 冒泡进来）。 */
  onContextMenu?: (e: React.MouseEvent) => void;
  /** 多选模式开着（.msgs.selecting 由 ChatStream 挂在容器上；这里管行内交互）。 */
  selecting?: boolean;
  /** 本条被选中 → .mgroup.sel（sel-box 实心 + 对勾）。 */
  selected?: boolean;
  /** 多选模式下点击消息本身（非右键）→ 切换选中态（README §3.4）。 */
  onToggleSelect?: () => void;

  // ── [v0.40.1] 引用块 + 转发（仅用户气泡） ──

  /** [v0.40.1] 引用发送 → 气泡内顶部的 .qref（微信式引用块），点它跳回原文。 */
  quote?: { name: string; text: string; ref?: string };
  /** 点 .qref → 跳回被引用消息（滚动 + 闪烁）；ChatStream 注入。 */
  onJumpQuote?: (ref: string) => void;
  /** [v1.0.23.1] 这条用户气泡其实是转发消息：显示引用窗（来源 header + 原文），配言是主文案。 */
  forwarded?: ForwardMeta;
  /** [v1.0.19.4] 用户随消息带的本地附件（路径+签名，无字节）——气泡下方渲染本地附件卡。 */
  attachments?: AttachmentInput[];

  // ── [v1.0.23.3] 推理模块 + 四方向按钮模块 ──

  /** [v1.0.23.3] 推理全文（message 落定权威值）；无 → 推理模块不渲染。 */
  reasoning?: string;
  /** 思考耗时（秒）。 */
  reasoningSeconds?: number;
  /** [v1.0.24.4-r13] 派卡接力：推理面板强制展开（定格不折叠，供收起动画从完整高度开始）。 */
  forceReasoningOpen?: boolean;
  /** 四方向建议卡片（辅助 LLM 异步生成，瞬时不落盘）。 */
  suggestions?: SuggestionItem[];
  /** 点击卡片发送（由 ChatStream 组装 title+sub+@mention 并走发送通道）。 */
  onSuggestionSend?: (text: string) => void;

  // ── [v1.0.23.4] 统一壳：流式期间也用 bubble agent tail（不换壳、不跳闪） ──

  /** [v1.0.23.4] 流式进行中：同一气泡壳内 三点 → 推理(live) → 落定正文 morph。 */
  streaming?: boolean;
  /** [v1.0.23.4] live-only 首帧保护 id（transient frame 机制，原 StreamBubble 职责）。 */
  frameId?: string;
  /** 过程态完成首帧后回执给状态层。 */
  onFramePaint?: (frameId: string) => void;
  /** 权威终态已到但首帧尚未确认（保留兼容，不再抑制推理显示）。 */
  settling?: boolean;
}

export const MessageBubble: React.FC<MessageBubbleProps> = React.memo(
  ({
  kind, text, grouped = false, tail = true, delivery, face, agentId, onOpenDm, onAgentContextMenu,
  files, projectId, domSeq,
  itemKey, onContextMenu, selecting = false, selected = false, onToggleSelect,
  quote, onJumpQuote, forwarded, attachments,
  reasoning, reasoningSeconds, suggestions, onSuggestionSend,
  streaming = false, frameId, onFramePaint, forceReasoningOpen = false,
}) => {
  const { t } = useCachedT();

  // [v1.0.23.4] transient frame 首帧保护（原 StreamBubble 职责，统一壳后在此回执）
  useEffect(() => {
    if (!frameId || !onFramePaint) return undefined;
    let cancelled = false;
    let raf = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const acknowledge = (): void => {
      if (!cancelled) onFramePaint(frameId);
    };
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
  // 多选模式下点消息本身 = 切换选中（README §3.4）；右键仍走菜单，不触发切换。
  const rowClick = selecting && onToggleSelect ? onToggleSelect : undefined;

  // [v0.40.1] 气泡内引用块（.qref）：微信式，被引用人名字加粗 + 原文预览，点击跳回。
  const qrefBlock = quote ? (
    <span
      className="qref"
      role={quote.ref ? 'button' : undefined}
      title={quote.ref ? t('message.bubble.07') : undefined}
      onClick={quote.ref ? (e) => { e.stopPropagation(); onJumpQuote?.(quote.ref as string); } : undefined}
    >
      <b>{quote.name}</b>{quote.text}
    </span>
  ) : null;

  // ── 用户消息：右对齐实心气泡 ──
  if (kind === 'user') {
    // [v1.0.23.1] 转发主文案 = 配言（text 已是 comment）。旧数据（无 comment 字段）保持
    //   原逻辑：markdown 原文按 Markdown 渲染（兼容 v0.40.1 时代的转发气泡）。
    const isNewForward = forwarded?.comment !== undefined;
    const userBody = !isNewForward && forwarded?.markdown
      ? <Markdown text={linkifyBareUrls(text)} />
      : text.split('\n').map((line, i) => (
        <React.Fragment key={i}>{i > 0 && <br />}{linkifyLineToNodes(line, `l${i}-`)}</React.Fragment>
      ));

    // [v1.0.23.1] 转发引用窗（权威参考配色）：header「↗ 转发自 {群名} · {来源者}」+
    //   原文内容（Agent 富文本走 Markdown）+ 被转发文件卡。取代旧 fwd-tag 小标签。
    const forwardQuote = forwarded ? (
      <div className="fwd-quote">
        <div className="fwd-quote-h">
          <IconForward />
          <span>转发自 {forwarded.sourceProjectName ? `${forwarded.sourceProjectName} · ` : ''}{forwarded.sourceName}</span>
        </div>
        {forwarded.originalText && (
          <div className="fwd-quote-b">
            {/* [v1.0.23.2] 原文一律 Markdown 渲染（不再看 forwarded.markdown 标志——
                用户原文里的 **加粗**、# 标题等符号不再裸露，Markdown 对纯文本同样安全） */}
            <Markdown text={linkifyBareUrls(forwarded.originalText)} />
          </div>
        )}
        {/* [v1.0.23.1] 被转发文件卡移进引用窗（文件属于被转发内容，不属于配言） */}
        {files && files.length > 0 && (
          <div className="fwd-quote-files">
            <FileCardList files={files} projectId={projectId || ''} />
          </div>
        )}
      </div>
    ) : null;

    return (
      <div
        className={'mgroup enter' + (grouped ? ' same' : '') + (selected ? ' sel' : '')}
        data-seq={domSeq}
        data-ik={itemKey}
        onContextMenu={onContextMenu}
        onClick={rowClick}
      >
        <div className="mrow me">
          <div className="sel-box"><IconCheck /></div>
          <div className="m-time" />
          {delivery === 'pending' && (
            <div className="m-sending" role="status" aria-label={t('message.bubble.01')} />
          )}
          {delivery === 'suspect' && (
            <div className="m-fail" title={t('message.bubble.05')} role="alert" aria-label={t('message.bubble.02')}>
              <IconAlert />
            </div>
          )}
          {/* [v0.5 #4] 用户自己打的字**不做 Markdown 解释**（转发自 Agent 的除外，见 userBody）。
              [v0.8d #3] 但贴进来的长日志一样会刷屏，所以他自己的气泡也能折。 */}
          <FoldableBubble className={'bubble me' + (tail ? ' tail' : '')} text={text}>
            {/* [v0.40.1] 引用块在最上（reference 把 .qref 放在气泡正文之上） */}
            {qrefBlock}
            {/* [v1.0.23.1] 转发引用窗（取代 fwd-tag）：来源 + 原文 */}
            {forwardQuote}
            {userBody}
            {/* [v1.0.19.4] 用户上传的本地附件卡片（点开走系统默认程序） */}
            {attachments && attachments.length > 0 && (
              <div className="fwd-files">
                <AttachmentCardList files={attachments} />
              </div>
            )}
          </FoldableBubble>
        </div>
      </div>
    );
  }

  // ── Agent 消息：左对齐，首条带名字与头像 ──
  const f: AgentFace = face ?? { name: 'Agent', role: 'Agent', glyph: '?', pal: 'av-d' };

  // [v0.37] 双击进私聊：只有 ChatStream 在群聊里传了 onOpenDm + agentId 才启用。
  //   发送者行和头像变成「可按」的：双击进私聊，按下时有 scale(0.95) 的轻回弹（纯 CSS :active）。
  const canDm = !!onOpenDm && !!agentId;
  const openDm = (): void => { if (agentId && onOpenDm) onOpenDm(agentId); };
  const canOpenAgentMenu = !!onAgentContextMenu && !!agentId;
  const openAgentContextMenu = (e: React.MouseEvent): void => {
    // .mgroup 自己已有 msgMenu；先阻止默认菜单和冒泡，再打开独立的 Agent 菜单。
    e.preventDefault();
    e.stopPropagation();
    if (agentId && onAgentContextMenu) onAgentContextMenu(e, agentId);
  };

  return (
    <div
      className={'mgroup enter' + (grouped ? ' same' : '') + (selected ? ' sel' : '')}
      data-seq={domSeq}
      data-ik={itemKey}
      onContextMenu={onContextMenu}
      onClick={rowClick}
    >
      {/* [v0.7 #3] 名字里已经有「项目经理」了就不再拼一次 —— 见 senderLineOf() */}
      {!grouped && (
        <div
          className={'sender-line' + (canDm ? ' dm-pressable' : '')}
          onDoubleClick={canDm ? openDm : undefined}
          title={canDm ? t('roster.panel.doubleClickDm', { name: f.name }) : undefined}
          role={canDm ? 'button' : undefined}
          tabIndex={canDm ? 0 : undefined}
          onKeyDown={canDm ? (e) => { if (e.key === 'Enter') openDm(); } : undefined}
        >
          {senderLineOf(f)}
        </div>
      )}
      <div className={'mrow' + (grouped ? ' cont' : '')}>
        <div className="sel-box"><IconCheck /></div>
        <div
          className={'m-av' + (canDm && !grouped ? ' dm-pressable' : '')}
          onDoubleClick={canDm && !grouped ? openDm : undefined}
          onContextMenu={canOpenAgentMenu && !grouped ? openAgentContextMenu : undefined}
          title={canDm && !grouped ? t('roster.panel.doubleClickDm', { name: f.name }) : undefined}
        >
          {!grouped && <Avatar glyph={f.glyph} pal={f.pal} size={36} title={f.name} src={f.avatarUrl} />}
        </div>
        {/*
          [v0.36] 气泡与文件卡片同属一根**竖列**（.m-content）：卡片挂在气泡正下方，
          和气泡左对齐、同宽约束。text 为空但有文件时（Worker 只写了文件没说话），
          干脆不画气泡，只留一张卡——空气泡不该出现在屏幕上。
        */}
        <div className="m-content">
          {/* [v1.0.23.3] 三段同气泡：推理面板(before) + 正文(bubble-body) + 建议卡片(after)。
              [v1.0.23.4] 流式期间同壳渲染：streaming 时内容 = 推理面板(live)/三点，
              落定后同一 DOM 节点 morph 出正文与卡片——不再切换 typing-bubble 壳（消除跳闪）。
              渲染条件放宽：streaming（三点阶段）或有推理或有正文都画气泡。 */}
          {(streaming || reasoning || text) ? (
            <FoldableBubble
              className={'bubble agent' + (tail ? ' tail' : '')}
              text={text}
              before={reasoning ? (
                <ReasoningPanel text={reasoning} seconds={reasoningSeconds} initiallyExpanded={forceReasoningOpen} />
              ) : undefined}
              after={suggestions && suggestions.length > 0 && onSuggestionSend ? (
                <Suggestions items={suggestions} onSend={onSuggestionSend} />
              ) : undefined}
              streaming={streaming}
              streamingReasoning={streaming ? reasoning : undefined}
            >
              {/* [v0.5 #4] agent 的回复走 Markdown（加粗/列表/代码块/换行）。
                 [v0.8d #3] 太长就折起来：几十行代码 + 一张表 + 一段长文，撑出去的那一屏
                 会把上文整个顶走——用户丢掉的是「刚才说到哪儿了」。 */}
              {text ? <Markdown text={linkifyBareUrls(text)} /> : null}
            </FoldableBubble>
          ) : null}
          {files && files.length > 0 && (
            <FileCardList files={files} projectId={projectId || ''} />
          )}
        </div>
        <div className="m-time" />
      </div>
    </div>
  );
  },
  // [v1.0.23.6] 自定义比较：只比数据 props（函数/上下文回调每次渲染新建，不比）。
  //   流式期间仅 streaming 气泡重渲染，其余气泡（含 70K 推理面板）跳过——主线程不再卡。
  (prev, next) => {
    if (prev.kind !== next.kind || prev.text !== next.text || prev.grouped !== next.grouped
      || prev.tail !== next.tail || prev.delivery !== next.delivery || prev.agentId !== next.agentId
      || prev.files !== next.files || prev.projectId !== next.projectId || prev.domSeq !== next.domSeq
      || prev.itemKey !== next.itemKey || prev.selecting !== next.selecting || prev.selected !== next.selected
      || prev.quote !== next.quote || prev.forwarded !== next.forwarded
      || prev.attachments !== next.attachments || prev.reasoning !== next.reasoning
      || prev.reasoningSeconds !== next.reasoningSeconds || prev.suggestions !== next.suggestions
      || prev.streaming !== next.streaming || prev.frameId !== next.frameId
      || prev.settling !== next.settling) return false;
    const fa = prev.face;
    const fb = next.face;
    if (!!fa !== !!fb) return false;
    if (fa && fb && (fa.name !== fb.name || fa.role !== fb.role || fa.glyph !== fb.glyph
      || fa.pal !== fb.pal || fa.avatarUrl !== fb.avatarUrl)) return false;
    return true;
  },
);

export default MessageBubble;
