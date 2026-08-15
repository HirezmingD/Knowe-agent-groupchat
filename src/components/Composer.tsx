/**
 * Composer.tsx — 输入区（component-tree §D · Composer）
 *
 * DOM：.composer-wrap > #quoteHolder + .attach-strip
 *                     + (.composer(.focus) > (.tools > button.icon-btn ×N) + textarea + button.send(.idle))
 *
 * 铁律（v0.3 计划 §阶段5，血的教训）：
 *   1. ★ 永不因 conn 从 DOM 卸载 —— 断线时输入框必须还在，字不能丢。
 *      （v0.2 的「输入框消失」事故就是把 Composer 挂在连接状态下渲染的。）
 *   2. 发送 → store.sendMessage：socket 出站 + 乐观 pending 气泡（store 内一并完成）。
 *      回声 5s 未到 → transport 的哨兵 → suspectEcho → 气泡变「发送存疑 ⚠」。
 *   3. 未连接也允许发送：transport 会响亮失败并进哨兵，屏幕上看得见——
 *      比「按钮变灰、用户不知道为什么」诚实。
 *   4. ★ [v0.7 #1] 输入框里的字**属于会话，不属于输入框**——它存在 conv.draft 里。
 *      切走保留、切回来还在；发出去才清。Composer 自己不留任何一份文字的副本。
 *   5. [v0.8b #9] **Ctrl/⌘+Enter 发送，Enter 换行。** 给 Agent 的指令是要分条写、
 *      要贴代码、要改两遍的——Enter 直接发，等于每敲一次回车就推出去半句话。
 *
 * textarea 自增高：用 ref 直接改 element.style.height（imperative），
 * 不是 JSX 的 style={{}}——铁律 1 禁的是后者。
 */

import React, {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import { roleLabel } from '../shared/roleLabel';
import { SEND_KEY } from '../shared/platform';
import { useKnoweStore } from '../store/store';
import {
  selectActiveMembers, selectActiveProjectId, selectConn, makeSelectDraft,
  makeSelectAttachments,
} from '../store/selectors';
import { IconClip, IconAt, IconUp, IconExpand, IconCollapse, IconX } from './icons';
import { toast } from './ContextMenu';
import { IconForKind } from '../preview/icons';
import { kindOf } from '../preview/fileKinds';
import { PLATFORM_PROJECT_ID } from '../store/avatar';
import type { Member } from '../store/state';

/** 输入区最小高度（设计稿的原状） */
export const COMPOSER_MIN = 96;

/** 没展开时，textarea 自增高的天花板 */
const MAX_TA_HEIGHT = 132;

/**
 * [v0.8b #5] 展开时，输入区顶到**离 chat-head 还有这么远**的地方。
 *
 * 留这条缝有两个用处：一是消息流不至于被压成 0 高（还看得见最后一条），
 * 二是「不覆盖、不重叠」——它是顶在头下面，不是压在头上面。
 */
const EXPAND_GAP = 24;

/** @候选浮层的碰边参数。浮层默认在触发按钮上方，空间不够时自动翻到下方。 */
const MENTION_PICKER_GAP = 8;
const MENTION_PICKER_MAX_HEIGHT = 280;
const MENTION_PICKER_VIEWPORT_MARGIN = 12;

interface MentionPickerPosition {
  left: number;
  top: number;
  maxHeight: number;
  placement: 'top' | 'bottom';
  ready: boolean;
}

interface MentionFragment {
  start: number;
  end: number;
  query: string;
}

/** 光标前是否正处在一个尚未完成的 @查询中（邮箱 local-part 不算）。 */
function mentionFragmentAt(value: string, cursor: number): MentionFragment | null {
  const before = value.slice(0, Math.max(0, cursor));
  const start = before.lastIndexOf('@');
  if (start < 0) return null;
  const prev = start > 0 ? before[start - 1] : '';
  if (/[A-Za-z0-9._%+-]/.test(prev)) return null; // foo@bar.com

  const query = before.slice(start + 1);
  // 打了空格/换行/标点，说明提及已经结束；显示名带空格仍可通过按钮列表插入。
  if (/[\s，。！？、,;；:：()（）\[\]{}]/.test(query) || query.length > 32) return null;
  return { start, end: cursor, query };
}

function mentionLabel(member: Member): string {
  // [v1.0.21.1.3] 一律显示成员显示名（项目经理就叫「项目经理」）。历史曾把 coordinator
  // 特判显示为「主管」——用户从未有此诉求，是遗留错误，已移除。
  // 后端 mentions 解析仍兼容「主管」等历史别名，手打 @主管 依然有效。
  return member.display.name.replace(/^@+/, '').trim();
}

export const Composer: React.FC = () => {
  const { t } = useTranslation();
  const sendMessage = useKnoweStore((s) => s.sendMessage);
  const projectId = useKnoweStore(selectActiveProjectId);
  const conn = useKnoweStore(selectConn);
  const members = useKnoweStore(selectActiveMembers);

  /*
   * [v0.7 #1] ★ 输入框里的字**不再是 Composer 自己的 state** —— 它归会话所有。
   *
   *   原来是 useState('')：一个输入框，一份文字，服务所有项目。
   *   在 A 群打了一半的字，切到 B 群，字跟着过去了；一按回车，发到的是 B。
   *   微信不是这样的——每个聊天各记各的草稿，切走了还在，切回来接着写。
   *
   *   所以文字进 store（conv.draft），Composer 只是它的一块玻璃：
   *   activeProjectId 一变，读到的自然就是新会话的草稿，不用手动"保存/恢复"。
   */
  const selectDraft = useMemo(() => makeSelectDraft(projectId), [projectId]);
  const text = useKnoweStore(selectDraft);
  const setDraft = useKnoweStore((s) => s.setDraft);
  const clearDraft = useKnoweStore((s) => s.clearDraft);

  /*
   * [v1.0.19.4] 待发送附件也归会话（和草稿一个道理）：在 A 群挂了两个文件、切到 B 群，
   *   文件跟着 A 群留着；切回来还在，发出去才清。Composer 只是它的一块玻璃。
   */
  const selectAttachments = useMemo(() => makeSelectAttachments(projectId), [projectId]);
  const attachments = useKnoweStore(selectAttachments);
  const addAttachments = useKnoweStore((s) => s.addAttachments);
  const removeAttachment = useKnoweStore((s) => s.removeAttachment);
  const clearAttachments = useKnoweStore((s) => s.clearAttachments);

  // Electron 里才有 selectFiles；浏览器兜底时附件按钮保持禁用（不假装能用）。
  const canAttach = typeof window !== 'undefined'
    && Boolean((window as unknown as { knowe?: { selectFiles?: unknown } }).knowe?.selectFiles);

  const remindMultimodal = useCallback(() => {
    // DESIGN 决策 #6：选/拖文件时提醒一句，不是所有模型都吃多模态。
    toast(t('composer.multimodalWarn'));
  }, []);

  const pickFiles = useCallback(async () => {
    if (!projectId) return;
    const bridge = (window as unknown as {
      knowe?: { selectFiles?: () => Promise<Array<Record<string, unknown>>> };
    }).knowe;
    if (!bridge?.selectFiles) return;
    try {
      const picks = await bridge.selectFiles();
      if (Array.isArray(picks) && picks.length) {
        addAttachments(projectId, picks as never);
        remindMultimodal();
      }
    } catch {
      toast(t('composer.openPickerFailed'));
    }
  }, [projectId, addAttachments, remindMultimodal]);

  /*
   * [v0.40.0] 引用条 + 多选禁用。
   *   · quote：右键「引用」写进 store，这里画在输入框上方（#quoteHolder，
   *     component-tree §D：.quote-bar > (.qb-body > .qb-nm + .qb-tx) + .qb-x）。
   *   · selecting：多选模式下整个输入区半透明、不可点（README §3.4；
   *     照抄 reference enterSelect 对 #composerWrap 的处理：opacity .4 + pointer-events none）。
   */
  const quote = useKnoweStore((s) => s.quote);
  const clearQuote = useKnoweStore((s) => s.clearQuote);
  const selecting = useKnoweStore((s) => s.selecting);

  const [focus, setFocus] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const composerBoxRef = useRef<HTMLDivElement>(null);
  const mentionButtonRef = useRef<HTMLButtonElement>(null);
  const mentionPickerRef = useRef<HTMLDivElement>(null);

  // [v0.44.5] @成员只属于项目群：私聊频道和知知平台会话不展示这套入口。
  const canMention = Boolean(
    projectId
    && projectId !== PLATFORM_PROJECT_ID
    && !projectId.startsWith('dm:'),
  );
  const mentionMembers = useMemo(
    () => members.filter((member) => member.status !== 'removed' && Boolean(mentionLabel(member))),
    [members],
  );
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionIndex, setMentionIndex] = useState(0);
  const [mentionPickerPosition, setMentionPickerPosition] = useState<MentionPickerPosition>({
    left: 12,
    top: 0,
    maxHeight: MENTION_PICKER_MAX_HEIGHT,
    placement: 'top',
    ready: false,
  });

  const filteredMentionMembers = useMemo(() => {
    const query = mentionQuery.trim().toLocaleLowerCase();
    if (!query) return mentionMembers;
    return mentionMembers.filter((member) => (
      mentionLabel(member).toLocaleLowerCase().includes(query)
      || (member.display.role + ' ' + roleLabel(member.display.role)).toLocaleLowerCase().includes(query)
      || member.id.toLocaleLowerCase().includes(query)
    ));
  }, [mentionMembers, mentionQuery]);

  const grow = useCallback(() => {
    const ta = taRef.current;
    if (!ta) return;
    // 空输入：placeholder 长文本（英文）会把 scrollHeight 撑成两行 →
    // 直接回落 rows=1 的固有单行高度，不读 scrollHeight。
    if (!ta.value) {
      ta.style.height = '';
      return;
    }
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, MAX_TA_HEIGHT) + 'px';
  }, []);

  // 换会话 → 输入框里换成另一段草稿 → 高度得跟着那段字重算（不然会留着上一条的高度）
  useEffect(() => { grow(); }, [projectId, grow]);

  // [v0.40.0] 右键「引用」→ 光标进输入框（照抄 reference setQuote 末尾的 ta.focus()）
  useEffect(() => { if (quote) taRef.current?.focus(); }, [quote]);

  // [v1.0.24.7-P0-3] 引用条跨会话错发·组件层双保险：store.switchProject 已治本（切群清
  //   quote），这里兜底「任何不经 switchProject 的会话变化」（如会话被归档/重建）。
  useEffect(() => {
    if (quote && quote.projectId !== projectId) clearQuote();
  }, [projectId, quote, clearQuote]);

  // 换群、成员全部归档，或点击组件外部 → 收起 @候选。
  useEffect(() => {
    setMentionOpen(false);
    setMentionQuery('');
    setMentionIndex(0);
  }, [projectId]);

  useEffect(() => {
    const closeOutside = (event: PointerEvent): void => {
      const root = composerRef.current;
      if (root && event.target instanceof Node && !root.contains(event.target)) {
        setMentionOpen(false);
      }
    };
    window.addEventListener('pointerdown', closeOutside);
    return () => window.removeEventListener('pointerdown', closeOutside);
  }, []);

  useEffect(() => {
    if (!canMention || mentionMembers.length === 0) setMentionOpen(false);
  }, [canMention, mentionMembers.length]);

  useEffect(() => {
    if (mentionIndex >= filteredMentionMembers.length) setMentionIndex(0);
  }, [filteredMentionMembers.length, mentionIndex]);

  const syncMentionFromCursor = useCallback((value: string, cursor: number | null): void => {
    if (!canMention || cursor === null) {
      setMentionOpen(false);
      return;
    }
    const fragment = mentionFragmentAt(value, cursor);
    if (!fragment) {
      setMentionOpen(false);
      setMentionQuery('');
      return;
    }
    setMentionQuery(fragment.query);
    setMentionIndex(0);
    setMentionOpen(true);
  }, [canMention]);

  const insertMention = useCallback((member: Member): void => {
    if (!projectId) return;
    const ta = taRef.current;
    const start = ta?.selectionStart ?? text.length;
    const end = ta?.selectionEnd ?? start;
    const fragment = mentionFragmentAt(text, start);
    const replaceStart = fragment?.start ?? start;
    const replaceEnd = fragment?.end ?? end;
    const token = `@${mentionLabel(member)} `;
    const next = text.slice(0, replaceStart) + token + text.slice(replaceEnd);
    const nextCursor = replaceStart + token.length;

    setDraft(projectId, next);
    setMentionOpen(false);
    setMentionQuery('');
    setMentionIndex(0);
    requestAnimationFrame(() => {
      const current = taRef.current;
      if (!current) return;
      current.focus();
      current.setSelectionRange(nextCursor, nextCursor);
      grow();
    });
  }, [projectId, text, setDraft, grow]);

  // [v1.0.23.9] 头像右键菜单「@ 备注名」→ 把 @名字 插进输入框光标处。
  // ContextMenu 发 knowe:insert-mention（带 agentId），这里复用 insertMention。
  useEffect(() => {
    const onInsertMention = (event: Event): void => {
      const detail = (event as CustomEvent<{ agentId?: string }>).detail;
      if (!detail?.agentId || !canMention) return;
      const member = mentionMembers.find((m) => m.id === detail.agentId);
      if (member) insertMention(member);
    };
    window.addEventListener('knowe:insert-mention', onInsertMention);
    return () => window.removeEventListener('knowe:insert-mention', onInsertMention);
  }, [canMention, mentionMembers, insertMention]);

  const toggleMentionPicker = useCallback((): void => {
    if (!canMention || mentionMembers.length === 0) return;
    if (mentionOpen) {
      setMentionOpen(false);
      setMentionQuery('');
      return;
    }
    if (!projectId) return;

    const ta = taRef.current;
    const start = ta?.selectionStart ?? text.length;
    const end = ta?.selectionEnd ?? start;
    const fragment = mentionFragmentAt(text, start);

    // @按钮不是单纯弹菜单：在光标处落下一个真实的 @，这样后续键入会自然变成过滤词，
    // 也不会被 textarea 的 onFocus（看见“没有 @”）立即把刚打开的菜单关掉。
    let next = text;
    let nextCursor = start;
    if (!fragment) {
      next = text.slice(0, start) + '@' + text.slice(end);
      nextCursor = start + 1;
      setDraft(projectId, next);
    }

    setMentionOpen(true);
    setMentionQuery(fragment?.query ?? '');
    setMentionIndex(0);
    requestAnimationFrame(() => {
      const current = taRef.current;
      if (!current) return;
      current.focus();
      current.setSelectionRange(nextCursor, nextCursor);
      grow();
    });
  }, [canMention, mentionMembers.length, mentionOpen, projectId, text, setDraft, grow]);

  const doSend = useCallback(() => {
    const t = text.trim();
    // [v1.0.19.4] 只要有文字**或**有附件就能发；纯附件（无文字）也允许。
    if ((!t && attachments.length === 0) || !projectId) return;
    setMentionOpen(false);
    setMentionQuery('');
    /*
     * [v0.40.1] 引用发送（README §4）——两条路分开：
     *   · 发给后端的正文 = 结构化引用（Agent 在后台看到明确的引用/发言分隔）：
     *       用户引用了 {名字} 的 "{完整原文}" ，用户说："{本轮正文}"
     *   · 气泡上显示的 = 用户本轮正文 + 顶部一条 .qref 引用块（displayText + quote，见 store.sendMessage）。
     */
    if (quote) {
      const structured = i18n.t('composer.quoteStructure', { name: quote.name, text: quote.text, verb: i18n.t('composer.17'), t });
      sendMessage(structured, projectId, undefined, {
        displayText: t,
        quote: {
          name: quote.name,
          text: quote.text.length > 80 ? `${quote.text.slice(0, 80)}…` : quote.text,
          ref: quote.itemKey,
        },
        ...(attachments.length ? { attachments: attachments as never } : {}),
      });
      clearQuote();
    } else {
      // 出站 + 乐观气泡（store 一手包办）；[v1.0.19.4] 带上本会话的附件。
      sendMessage(t, projectId, undefined,
        attachments.length ? { attachments: attachments as never } : undefined);
    }
    clearDraft(projectId);              // [v0.7 #1] 发出去了，草稿就没了——列表的临时置顶也跟着撤
    if (attachments.length) clearAttachments(projectId);   // [v1.0.19.4] 附件发出即清
    const ta = taRef.current;
    if (ta) ta.style.height = 'auto';
  }, [text, projectId, sendMessage, clearDraft, quote, clearQuote, attachments, clearAttachments]);

  const idle = !text.trim() && attachments.length === 0;

  /*
   * [v0.5 #11/#15] 展开：写长东西时，一行半的输入框根本不够用。
   *
   * 展开后输入区顶到聊天区的 ~80%（只留顶上群聊名那一行可见），再点收回去。
   * 另外那条分隔线也能直接上下拖（#15）——两条路通向同一个状态：
   * 都只是改 `--composer-h` 这个 CSS 变量。拖拽时不走 React state（每像素
   * setState 会把整条消息流重渲染，手感是黏的），松手才落一次。
   */
  const [expanded, setExpanded] = useState(false);
  const dragRef = useRef<{ y: number; h: number } | null>(null);

  /*
   * [v0.8b #5] 展开之后到底能有多高，**得问屏幕**，不能拍脑袋写 80vh。
   *
   *   老代码：展开 = window.innerHeight * 0.8。可输入区住在 .chat-card 里，
   *   上面还压着标题栏、外面还有边距——0.8 个窗口高既不是「顶到 chat-head」，
   *   在小窗口下还会把消息流挤没。而且更要命的是：`--composer-h` 这个变量
   *   **CSS 里根本没人读**（只有 :root 里一句默认值），所以拖那条分隔线拖了个寂寞。
   *
   *   现在：量出聊天卡的可用高度（卡高 − 头高 − 一条缝），CSS 用 --composer-h 定高。
   *   拖拽和展开走的是同一个变量，两条路终于通向同一个状态。
   */
  const maxComposerHeight = useCallback(() => {
    const card = document.querySelector('.chat-card') as HTMLElement | null;
    const head = document.querySelector('.chat-head') as HTMLElement | null;
    if (!card) return Math.max(COMPOSER_MIN, window.innerHeight * 0.8);
    const avail = card.clientHeight - (head?.offsetHeight ?? 64) - EXPAND_GAP;
    return Math.max(COMPOSER_MIN, avail);
  }, []);

  const setComposerHeight = useCallback((px: number) => {
    const h = Math.max(COMPOSER_MIN, Math.min(maxComposerHeight(), px));
    document.documentElement.style.setProperty('--composer-h', `${h}px`);
    return h;
  }, [maxComposerHeight]);

  const toggleExpand = useCallback(() => {
    setExpanded((was) => {
      const next = !was;
      // 展开 = 一路顶到 chat-head 底下（差一条 EXPAND_GAP 的缝）
      setComposerHeight(next ? maxComposerHeight() : COMPOSER_MIN);
      return next;
    });
  }, [setComposerHeight, maxComposerHeight]);

  // 窗口大小变了 → 展开态的高度得跟着重算，否则会撑破或缩水
  useEffect(() => {
    if (!expanded) return;
    const onResize = (): void => { setComposerHeight(maxComposerHeight()); };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [expanded, setComposerHeight, maxComposerHeight]);

  // 分隔线：按住往上拖 = 变高
  const onDragStart = (e: React.PointerEvent): void => {
    e.preventDefault();
    const cur = parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue('--composer-h'),
    ) || COMPOSER_MIN;
    dragRef.current = { y: e.clientY, h: cur };
    document.body.classList.add('resizing-v');
  };

  useEffect(() => {
    const move = (e: PointerEvent): void => {
      const d = dragRef.current;
      if (!d) return;
      const h = setComposerHeight(d.h + (d.y - e.clientY));   // 往上拖 → 变高
      setExpanded(h > COMPOSER_MIN + 40);
    };
    const up = (): void => {
      if (!dragRef.current) return;
      dragRef.current = null;
      document.body.classList.remove('resizing-v');
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  }, [setComposerHeight]);

  /*
   * [v0.44.5] @候选浮层不再绑死在「整个输入框的上边框」。输入框展开后，那条
   * 上边框会升得很高，旧的 bottom:100% 会把 280px 浮层顶出 .chat-card，随后又被
   * overflow:hidden 裁掉。现在以真正的触发按钮为锚点，并以聊天卡片/窗口的交集
   * 作为可见边界：默认放上方；放不下但下方能放时翻转；两边都放不下时选空间较大
   * 的一侧并收紧 max-height。ResizeObserver 让拖高输入框时也能逐帧跟上。
   */
  const positionMentionPicker = useCallback((): void => {
    const box = composerBoxRef.current;
    const trigger = mentionButtonRef.current;
    const picker = mentionPickerRef.current;
    if (!box || !trigger || !picker) return;

    const boxRect = box.getBoundingClientRect();
    const triggerRect = trigger.getBoundingClientRect();
    const pickerRect = picker.getBoundingClientRect();
    const cardRect = (box.closest('.chat-card') as HTMLElement | null)?.getBoundingClientRect();
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;

    // .chat-card 自己 overflow:hidden，所以只看 window 还不够；两者取交集才是真可见区。
    const visibleTop = Math.max(
      MENTION_PICKER_VIEWPORT_MARGIN,
      (cardRect?.top ?? 0) + MENTION_PICKER_VIEWPORT_MARGIN,
    );
    const visibleBottom = Math.min(
      viewportHeight - MENTION_PICKER_VIEWPORT_MARGIN,
      (cardRect?.bottom ?? viewportHeight) - MENTION_PICKER_VIEWPORT_MARGIN,
    );
    const visibleLeft = Math.max(
      MENTION_PICKER_VIEWPORT_MARGIN,
      (cardRect?.left ?? 0) + MENTION_PICKER_VIEWPORT_MARGIN,
    );
    const visibleRight = Math.min(
      viewportWidth - MENTION_PICKER_VIEWPORT_MARGIN,
      (cardRect?.right ?? viewportWidth) - MENTION_PICKER_VIEWPORT_MARGIN,
    );

    const naturalHeight = Math.min(
      MENTION_PICKER_MAX_HEIGHT,
      Math.max(picker.scrollHeight, pickerRect.height) || MENTION_PICKER_MAX_HEIGHT,
    );
    const spaceAbove = Math.max(0, triggerRect.top - visibleTop - MENTION_PICKER_GAP);
    const spaceBelow = Math.max(0, visibleBottom - triggerRect.bottom - MENTION_PICKER_GAP);
    let placement: MentionPickerPosition['placement'] = 'top';
    if (naturalHeight > spaceAbove
        && (naturalHeight <= spaceBelow || spaceBelow > spaceAbove)) {
      placement = 'bottom';
    }
    const availableHeight = placement === 'top' ? spaceAbove : spaceBelow;
    const maxHeight = Math.max(0, Math.min(MENTION_PICKER_MAX_HEIGHT, availableHeight));
    const renderedHeight = Math.min(naturalHeight, maxHeight);

    const pickerWidth = pickerRect.width || Math.min(286, Math.max(0, boxRect.width - 24));
    const maxViewportLeft = Math.max(visibleLeft, visibleRight - pickerWidth);
    const viewportLeft = Math.min(Math.max(triggerRect.left, visibleLeft), maxViewportLeft);
    const viewportTop = placement === 'top'
      ? triggerRect.top - MENTION_PICKER_GAP - renderedHeight
      : triggerRect.bottom + MENTION_PICKER_GAP;

    const next: MentionPickerPosition = {
      left: Math.round(viewportLeft - boxRect.left),
      top: Math.round(viewportTop - boxRect.top),
      maxHeight: Math.floor(maxHeight),
      placement,
      ready: true,
    };
    setMentionPickerPosition((current) => (
      current.left === next.left
      && current.top === next.top
      && current.maxHeight === next.maxHeight
      && current.placement === next.placement
      && current.ready
        ? current
        : next
    ));
  }, []);

  useLayoutEffect(() => {
    if (!mentionOpen) return;

    let frame = 0;
    const schedule = (): void => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(positionMentionPicker);
    };

    // 首帧在绘制前定好位置，避免浮层先在旧坐标闪一下再跳。
    positionMentionPicker();
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('pointermove', schedule);

    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(schedule);
    [composerBoxRef.current, mentionButtonRef.current, mentionPickerRef.current]
      .forEach((element) => { if (element) observer?.observe(element); });

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', schedule);
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('pointermove', schedule);
      observer?.disconnect();
    };
  }, [mentionOpen, expanded, filteredMentionMembers.length, positionMentionPicker]);

  return (
    <div
      ref={composerRef}
      className={'composer-wrap' + (expanded ? ' expanded' : '')}
      /* [v0.40.0] 多选模式：输入区半透明不可用（照抄 reference 对 #composerWrap 的内联处理） */
      style={selecting ? { opacity: 0.4, pointerEvents: 'none' } : undefined}
      aria-disabled={selecting || undefined}
    >
      {/* [v0.5 #15] 聊天区与输入框之间的分隔线，上下可拖 */}
      <div
        className="composer-grip"
        role="separator"
        aria-orientation="horizontal"
        aria-label={t('composer.18')}
        tabIndex={0}
        onPointerDown={onDragStart}
        onDoubleClick={toggleExpand}
        onKeyDown={(e) => {
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault();
            const cur = parseFloat(
              getComputedStyle(document.documentElement).getPropertyValue('--composer-h'),
            ) || COMPOSER_MIN;
            const h = setComposerHeight(cur + (e.key === 'ArrowUp' ? 40 : -40));
            setExpanded(h > COMPOSER_MIN + 40);
          }
        }}
      />

      {/* [v0.40.0] 引用条（右键「引用」）。DOM 照 component-tree §D：
          .quote-bar > (.qb-body > .qb-nm + .qb-tx) + .qb-x（点 × 撤销引用）。 */}
      <div id="quoteHolder">
        {quote && (
          <div className="quote-bar">
            <div className="qb-body">
              <div className="qb-nm">{quote.name}</div>
              <div className="qb-tx">{quote.text}</div>
            </div>
            <div
              className="qb-x"
              role="button"
              tabIndex={0}
              aria-label={t('composer.06')}
              onClick={clearQuote}
              onKeyDown={(e) => { if (e.key === 'Enter') clearQuote(); }}
            >
              <IconX />
            </div>
          </div>
        )}
      </div>

      {/* [v1.0.19.4] 文件放置区域：输入框上方新展开的空间，文件卡左对齐、可换行，
          不占用正常输入框空间；移除全部后平滑收回（DESIGN §2.1）。 */}
      {attachments.length > 0 && (
        <div className="attach-strip" aria-label={t('composer.10')}>
          {attachments.map((file) => (
            <span className="attach-chip" key={file.path} title={file.path}>
              <span className="ac-icon" aria-hidden="true">
                <IconForKind kind={kindOf(file as never)} size={14} />
              </span>
              <span className="ac-name">{file.name}</span>
              {file.ext ? <span className="ac-ext">.{file.ext}</span> : null}
              <span
                className="ax"
                role="button"
                tabIndex={0}
                aria-label={t('composer.removeFileAria', { name: file.name })}
                onClick={() => removeAttachment(projectId!, file.path)}
                onKeyDown={(e) => { if (e.key === 'Enter') removeAttachment(projectId!, file.path); }}
              >
                <IconX />
              </span>
            </span>
          ))}
        </div>
      )}

      <div
        ref={composerBoxRef}
        className={'composer' + (focus ? ' focus' : '')}
        style={{ position: 'relative' }}
      >
        <div className="tools">
          <button
            className="icon-btn"
            aria-label={t('composer.16')}
            title={canAttach ? t('composer.16') : t('composer.09')}
            disabled={!canAttach || !projectId}
            onClick={() => { void pickFiles(); }}
          >
            <IconClip />
          </button>
          <button
            ref={mentionButtonRef}
            className="icon-btn"
            aria-label={t('composer.11')}
            aria-haspopup="listbox"
            aria-expanded={mentionOpen}
            title={canMention ? t('composer.12') : t('composer.01')}
            disabled={!canMention || mentionMembers.length === 0}
            onClick={toggleMentionPicker}
          >
            <IconAt />
          </button>
        </div>

        {mentionOpen && (
          <div
            ref={mentionPickerRef}
            className="mention-picker"
            role="listbox"
            aria-label={t('composer.19')}
            data-placement={mentionPickerPosition.placement}
            style={{
              left: mentionPickerPosition.left,
              top: mentionPickerPosition.top,
              maxHeight: mentionPickerPosition.maxHeight,
              visibility: mentionPickerPosition.ready ? 'visible' : 'hidden',
            }}
            onMouseDown={(event) => event.preventDefault()}
          >
            {filteredMentionMembers.length === 0 ? (
              <div className="mention-picker-empty">{t('composer.14')}</div>
            ) : filteredMentionMembers.map((member, index) => {
              const label = mentionLabel(member);
              const selected = index === mentionIndex;
              return (
                <button
                  key={member.id}
                  type="button"
                  className={'mention-picker-option' + (selected ? ' selected' : '')}
                  role="option"
                  aria-selected={selected}
                  onMouseEnter={() => setMentionIndex(index)}
                  onClick={() => insertMention(member)}
                >
                  <span className="mention-picker-at" aria-hidden="true">@</span>
                  <span className="mention-picker-copy">
                    <span className="mention-picker-name">{label}</span>
                    <span className="mention-picker-role">
                      {roleLabel(member.display.role) || (member.id === 'coordinator' ? t('common.06') : t('common.07'))}
                      {member.state === 'busy' ? ' · ' + t('composer.08') : ''}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        )}

        <textarea
          ref={taRef}
          rows={1}
          value={text}
          placeholder={conn === 'live'
            ? t('composer.02', { sendKey: SEND_KEY })
            : t('composer.03')}
          aria-label={t('composer.15')}
          onChange={(e) => {
            if (projectId) setDraft(projectId, e.target.value);
            syncMentionFromCursor(e.target.value, e.target.selectionStart);
            grow();
          }}
          onClick={(e) => syncMentionFromCursor(e.currentTarget.value, e.currentTarget.selectionStart)}
          onFocus={(e) => {
            setFocus(true);
            syncMentionFromCursor(e.currentTarget.value, e.currentTarget.selectionStart);
          }}
          onBlur={() => setFocus(false)}
          onKeyDown={(e) => {
            if (mentionOpen) {
              if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const count = filteredMentionMembers.length;
                if (count > 0) {
                  setMentionIndex((current) => (
                    e.key === 'ArrowDown' ? (current + 1) % count : (current - 1 + count) % count
                  ));
                }
                return;
              }
              if ((e.key === 'Enter' && !e.ctrlKey && !e.metaKey) || e.key === 'Tab') {
                const member = filteredMentionMembers[mentionIndex];
                if (member) {
                  e.preventDefault();
                  insertMention(member);
                  return;
                }
              }
              if (e.key === 'Escape') {
                e.preventDefault();
                setMentionOpen(false);
                return;
              }
            }

            /*
             * [v0.8b #9] Ctrl/⌘ + Enter 发送；Enter 单独按 = 换行。
             *
             *   跟 AI 说话和跟人发微信不是一回事：给 Agent 的指令常常是分条的、
             *   带代码的、要改两遍的。Enter 直接发，等于每敲一次回车就把半句话推出去。
             *   （这也是 ChatGPT / Claude / Cursor 的输入框都提供这个档位的原因。）
             */
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              doSend();
            }
          }}
        />

        {/* [v0.5 #11] 展开/收起——就在发送键左边 */}
        <button
          className="icon-btn composer-expand"
          aria-label={expanded ? t('composer.13') : t('composer.07')}
          aria-pressed={expanded}
          title={expanded ? t('composer.13') : t('composer.07')}
          onClick={toggleExpand}
        >
          {expanded ? <IconCollapse /> : <IconExpand />}
        </button>

        <button
          className={'send' + (idle ? ' idle' : '')}
          aria-label={t('composer.04')}
          title={t('composer.05', { sendKey: SEND_KEY })}          /* [v0.8b #9] */
          disabled={idle}
          onClick={doSend}
        >
          <IconUp />
        </button>
      </div>
    </div>
  );
};

export default Composer;
