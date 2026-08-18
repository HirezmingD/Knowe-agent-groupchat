/**
 * Composer.tsx — 输入区（component-tree §D · Composer）
 *
 * [v1.0.37.3] 输入内核 textarea → TipTap 富文本：
 *   · @成员 显示为 tag（mention 节点，退格整体删）
 *   · markdown 快捷输入（# 标题 / ** 加粗 / * 斜体 / - 列表 / 1. 列表 / ``` 代码块）
 *   · 右键原生编辑菜单（Electron IPC）
 *
 * DOM：.composer-wrap > #quoteHolder + .attach-strip
 *                     + (.composer(.focus) > (.tools > button.icon-btn ×N)
 *                         + .tiptap-wrap(EditorContent) + button.send(.idle))
 *
 * 铁律（v0.3 计划 §阶段5，血的教训）：
 *   1. ★ 永不因 conn 从 DOM 卸载 —— 断线时输入框必须还在，字不能丢。
 *   2. 发送 → store.sendMessage：socket 出站 + 乐观 pending 气泡（store 内一并完成）。
 *   3. 未连接也允许发送：transport 会响亮失败并进哨兵，屏幕上看得见。
 *   4. ★ [v0.7 #1] 输入框里的字**属于会话，不属于输入框**——它存在 conv.draft 里。
 *      [v1.0.37.3] draft 仍是纯文本 string（markdown）：编辑器是富文本真源，
 *      onUpdate 时序列化写回 draft；切会话时 markdown 反序列化恢复（见 tiptapMarkdown.ts）。
 *   5. [v0.8b #9] **Ctrl/⌘+Enter 发送，Enter 换行。**
 *
 * TipTap 集成注意（stale closure 铁律）：editorProps.handleKeyDown / onUpdate 捕获的是
 * 创建时的闭包——所有需要最新状态的操作一律经 ref 转发（*Ref.current），绝不直接闭包调用。
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
import { useEditor, EditorContent, type Editor } from '@tiptap/react';
import { Extension } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import Mention from '@tiptap/extension-mention';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import {
  tiptapDocToMarkdown, markdownToTiptapDoc, type MentionMatcher,
} from '../shared/tiptapMarkdown';

/** 输入区最小高度（设计稿的原状） */
export const COMPOSER_MIN = 96;

/** 没展开时，输入区自增高的天花板 */
const MAX_TA_HEIGHT = 132;

/**
 * [v0.8b #5] 展开时，输入区顶到**离 chat-head 还有这么远**的地方。
 */
const EXPAND_GAP = 24;

/** @候选浮层的碰边参数。浮层默认在触发上方，空间不够时自动翻到下方。 */
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

function mentionLabel(member: Member): string {
  // [v1.0.21.1.3] 一律显示成员显示名（项目经理就叫「项目经理」）。历史曾把 coordinator
  // 特判显示为「主管」——用户从未有此诉求，是遗留错误，已移除。
  // 后端 mentions 解析仍兼容「主管」等历史别名，手打 @主管 依然有效。
  return (member.display.name ?? '').replace(/^@+/, '').trim();
}

/**
 * [v1.0.37.3] 由「光标前文本」判断是否正处在一个尚未完成的 @查询中（邮箱 local-part 不算）。
 * 原 mentionFragmentAt 的文本版——textarea 时代基于 value+selectionStart，
 * 现在基于 TipTap 文档光标前的纯文本。
 */
function mentionQueryAt(before: string): { start: number; query: string } | null {
  const at = before.lastIndexOf('@');
  if (at < 0) return null;
  const prev = at > 0 ? (before[at - 1] ?? '') : '';
  if (/[A-Za-z0-9._%+-]/.test(prev)) return null; // foo@bar.com

  const query = before.slice(at + 1);
  // 打了空格/换行/标点，说明提及已经结束；显示名带空格仍可通过按钮列表插入。
  if (/[\s，。！？、,;；:：()（）\[\]{}]/.test(query) || query.length > 32) return null;
  return { start: at, query };
}

/**
 * [v1.0.37.3] 代码块行号：给 codeBlock 内每个非空行加 .code-line（data-line=真实行号），
 * CSS ::before 渲染行号。空行跳过（不显示数字，但不错号）。
 */
const codeLineNumbers = Extension.create({
  name: 'codeLineNumbers',
  addProseMirrorPlugins() {
    const linePlugin = new Plugin({
      key: new PluginKey('codeLineNumbers'),
      props: {
        decorations(state) {
          const decos: Decoration[] = [];
          state.doc.descendants((node, pos) => {
            if (node.type.name !== 'codeBlock') return;
            const lines = node.textContent.split('\n');
            let offset = 0;
            for (let i = 0; i < lines.length; i++) {
              const line = lines[i] ?? '';
              if (line.length > 0) {
                decos.push(
                  Decoration.inline(
                    pos + 1 + offset,
                    pos + 1 + offset + line.length,
                    { class: 'code-line', 'data-line': String(i + 1) },
                  ),
                );
              }
              offset += line.length + 1;
            }
          });
          return DecorationSet.create(state.doc, decos);
        },
      },
    });
    return [linePlugin];
  },
});

export const Composer: React.FC = () => {
  const { t } = useTranslation();
  const sendMessage = useKnoweStore((s) => s.sendMessage);
  const projectId = useKnoweStore(selectActiveProjectId);
  const conn = useKnoweStore(selectConn);
  const members = useKnoweStore(selectActiveMembers);

  /*
   * [v0.7 #1] ★ 输入框里的字**不再是 Composer 自己的 state** —— 它归会话所有。
   * [v1.0.37.3] draft 仍是纯文本（markdown）：编辑器是富文本真源，onUpdate 写回。
   */
  const selectDraft = useMemo(() => makeSelectDraft(projectId), [projectId]);
  const text = useKnoweStore(selectDraft);
  const setDraft = useKnoweStore((s) => s.setDraft);
  const clearDraft = useKnoweStore((s) => s.clearDraft);

  /* [v1.0.19.4] 待发送附件也归会话（和草稿一个道理）。 */
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

  /* [v0.40.0] 引用条 + 多选禁用。 */
  const quote = useKnoweStore((s) => s.quote);
  const clearQuote = useKnoweStore((s) => s.clearQuote);
  const selecting = useKnoweStore((s) => s.selecting);

  const [focus, setFocus] = useState(false);
  const composerRef = useRef<HTMLDivElement>(null);
  const composerBoxRef = useRef<HTMLDivElement>(null);
  const mentionButtonRef = useRef<HTMLButtonElement>(null);
  const mentionPickerRef = useRef<HTMLDivElement>(null);
  const editorElRef = useRef<HTMLDivElement>(null);

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

  /** [v1.0.37.3] 草稿恢复/发送用：成员显示名 → mention 匹配表。 */
  const mentionMatchers = useMemo<MentionMatcher[]>(
    () => mentionMembers.map((m) => ({ id: m.id, label: mentionLabel(m) })),
    [mentionMembers],
  );

  /** [v1.0.37.3] 编辑器是否有非空内容（发送键 idle 判断，避免吃 draft 影子的一帧滞后）。 */
  const [hasText, setHasText] = useState(false);

  /*
   * [v1.0.37.3] 自增高：textarea scrollHeight → contentEditable scrollHeight。
   * 空输入回落单行高（placeholder 长文本会把 scrollHeight 撑成两行）。
   */
  const grow = useCallback(() => {
    const wrap = editorElRef.current;
    const el = wrap?.querySelector<HTMLElement>('[contenteditable]') ?? wrap;
    if (!el) return;
    if (!el.innerText || el.innerText.trim() === '') {
      el.style.height = '';
      return;
    }
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, MAX_TA_HEIGHT) + 'px';
  }, []);

  /* ═══════════ TipTap 编辑器（创建一次，全部经 ref 转发防 stale closure） ═══════════ */

  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;
  const mentionOpenRef = useRef(mentionOpen);
  mentionOpenRef.current = mentionOpen;
  const mentionIndexRef = useRef(mentionIndex);
  mentionIndexRef.current = mentionIndex;
  const filteredMentionMembersRef = useRef(filteredMentionMembers);
  filteredMentionMembersRef.current = filteredMentionMembers;

  // 光标前 @ 查询 → 开关弹层（TipTap 文档文本版）。
  const syncMentionFromCursor = useCallback((ed: Editor): void => {
    if (!canMention) {
      setMentionOpen(false);
      return;
    }
    const { state } = ed.view;
    const pos = state.selection.$from.pos;
    const before = state.doc.textBetween(Math.max(0, pos - 40), pos);
    const frag = mentionQueryAt(before);
    if (!frag) {
      setMentionOpen(false);
      setMentionQuery('');
      return;
    }
    setMentionQuery(frag.query);
    setMentionIndex(0);
    setMentionOpen(true);
  }, [canMention]);
  const syncMentionRef = useRef(syncMentionFromCursor);
  syncMentionRef.current = syncMentionFromCursor;

  const insertMention = useCallback((member: Member): void => {
    const ed = editorRef.current;
    if (!ed || !projectId) return;
    const { state } = ed;
    const { from } = state.selection;
    // [v1.0.37.3 fix] 删除触发字符 @（及未完成的查询串 @小…），否则残留文本 @ 与
    // 胶囊自带的 @ 叠加成 @@。用 mentionQueryAt 定位 @ 起点，整段删干净。
    const before = state.doc.textBetween(Math.max(0, from - 40), from);
    const frag = mentionQueryAt(before);
    const deleteFrom = frag ? from - (before.length - frag.start) : from;
    // [v1.0.37.3 fix] 胶囊两侧补真实空格：浏览器 caret 不认 margin（紧邻 inline-block
    // 的插入点，caret 画在元素 border 处），只有真实空格才能让光标/文字离开胶囊。
    // 右侧始终补空格；左侧仅当非行首且前字符不是空格时补（行首不顶格）。
    const beforeChar = state.doc.textBetween(Math.max(0, deleteFrom - 1), deleteFrom);
    const leftSpace = beforeChar && !/\s$/.test(beforeChar) ? ' ' : '';
    // 动态构建：空文本节点会被 ProseMirror 拒绝（Empty text nodes are not allowed）
    const content: Array<{ type: string; text?: string; attrs?: Record<string, string> }> = [];
    if (leftSpace) content.push({ type: 'text', text: leftSpace });
    content.push({
      type: 'mention',
      attrs: { id: member.id, label: mentionLabel(member) },
    });
    content.push({ type: 'text', text: ' ' });
    ed.chain()
      .focus()
      .deleteRange({ from: deleteFrom, to: from })
      .insertContent(content)
      .run();
    setMentionOpen(false);
    setMentionQuery('');
    setMentionIndex(0);
  }, [projectId]);
  const insertMentionRef = useRef(insertMention);
  insertMentionRef.current = insertMention;

  const doSend = useCallback(() => {
    const ed = editorRef.current;
    if (!ed || !projectId) return;
    // [v1.0.37.3] 发送取编辑器真源（draft 是影子，可能滞后一帧）。
    const t = tiptapDocToMarkdown(ed.getJSON()).trim();
    // [v1.0.19.4] 只要有文字**或**有附件就能发；纯附件（无文字）也允许。
    if ((!t && attachments.length === 0)) return;
    setMentionOpen(false);
    setMentionQuery('');
    /*
     * [v0.40.1] 引用发送（README §4）——两条路分开：
     *   · 发给后端的正文 = 结构化引用（Agent 在后台看到明确的引用/发言分隔）
     *   · 气泡上显示的 = 用户本轮正文 + 顶部一条 .qref 引用块
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
    ed.commands.clearContent();          // 触发 onUpdate → setDraft('') + hasText=false
    clearDraft(projectId);               // [v0.7 #1] 发出去了，草稿就没了
    if (attachments.length) clearAttachments(projectId);   // [v1.0.19.4] 附件发出即清
    grow();
  }, [projectId, sendMessage, clearDraft, quote, clearQuote, attachments, clearAttachments, grow]);
  const doSendRef = useRef(doSend);
  doSendRef.current = doSend;

  // 弹层键盘导航 + 发送快捷键（handleKeyDown 是创建时闭包，全部读 ref）。
  const handleKeyDownRef = useRef<(_view: unknown, event: KeyboardEvent) => boolean>(() => false);
  handleKeyDownRef.current = (_view, event) => {
    if (mentionOpenRef.current) {
      const count = filteredMentionMembersRef.current.length;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const cur = mentionIndexRef.current;
        const next = event.key === 'ArrowDown' ? (cur + 1) % count : (cur - 1 + count) % count;
        setMentionIndex(next);
        return true;
      }
      if ((event.key === 'Enter' && !event.ctrlKey && !event.metaKey) || event.key === 'Tab') {
        const member = filteredMentionMembersRef.current[mentionIndexRef.current];
        if (member) {
          event.preventDefault();
          insertMentionRef.current(member);
          return true;
        }
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setMentionOpen(false);
        setMentionQuery('');
        return true;
      }
    }
    /*
     * [v0.8b #9] Ctrl/⌘ + Enter 发送；Enter 单独按 = 换行（TipTap 默认）。
     * 给 Agent 的指令常常是分条的、带代码的——Enter 直接发，等于每敲一次回车推半句话。
     */
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      doSendRef.current();
      return true;
    }
    return false;
  };

  const editorRef = useRef<Editor | null>(null);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3] },
      }),
      Placeholder.configure({
        placeholder: conn === 'live'
          ? t('composer.02', { sendKey: SEND_KEY })
          : t('composer.03'),
      }),
      Mention.configure({
        HTMLAttributes: { class: 'mention-tag' },
      }),
      codeLineNumbers,
    ],
    content: markdownToTiptapDoc(text, mentionMatchers),
    editorProps: {
      attributes: {
        'aria-label': t('composer.15'),
        class: 'tiptap-input',
      },
      handleKeyDown: (view, event) => handleKeyDownRef.current(view, event),
    },
    onUpdate: ({ editor: ed }) => {
      const md = tiptapDocToMarkdown(ed.getJSON());
      if (projectIdRef.current) setDraft(projectIdRef.current, md);
      setHasText(!!md.trim());
      syncMentionRef.current(ed);
      grow();
    },
    onSelectionUpdate: ({ editor: ed }) => { syncMentionRef.current(ed); },
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false),
    onCreate: () => { grow(); },
  }, []);

  editorRef.current = editor;

  // 换会话 → 编辑器换成另一段草稿（markdown 反序列化恢复）；mention 弹层收起。
  useEffect(() => {
    if (!editor || !projectId) return;
    const md = useKnoweStore.getState().convs[projectId]?.draft ?? '';
    editor.commands.setContent(markdownToTiptapDoc(md, mentionMatchers), { emitUpdate: false });
    setMentionOpen(false);
    setMentionQuery('');
    setMentionIndex(0);
    setHasText(!!md.trim());
    grow();
  }, [projectId, editor, mentionMatchers, grow]);

  // [v0.40.0] 右键「引用」→ 光标进输入框。
  useEffect(() => {
    if (quote) editor?.commands.focus();
  }, [quote, editor]);

  // [v1.0.24.7-P0-3] 引用条跨会话错发·组件层双保险：store.switchProject 已治本，这里兜底。
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

  // [v1.0.23.9] 头像右键菜单「@ 备注名」→ 把 @名字 插进输入框光标处。
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
    const ed = editorRef.current;
    if (!ed) return;
    if (mentionOpen) {
      setMentionOpen(false);
      setMentionQuery('');
      return;
    }
    // @按钮不是单纯弹菜单：在光标处落下一个真实的 @，后续键入会自然变成过滤词。
    ed.chain().focus().insertContent('@').run();
  }, [canMention, mentionMembers.length, mentionOpen]);

  const idle = !hasText && attachments.length === 0;

  /*
   * [v0.5 #11/#15] 展开：写长东西时，一行半的输入框根本不够用。
   * 展开后输入区顶到聊天区的 ~80%（只留顶上群聊名那一行可见），再点收回去。
   * 两条路（展开键/拖分隔线）通向同一个状态：都只是改 --composer-h 这个 CSS 变量。
   */
  const [expanded, setExpanded] = useState(false);
  const dragRef = useRef<{ y: number; h: number } | null>(null);

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
      setComposerHeight(next ? maxComposerHeight() : COMPOSER_MIN);
      return next;
    });
  }, [setComposerHeight, maxComposerHeight]);

  // 窗口大小变了 → 展开态的高度得跟着重算。
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
      const h = setComposerHeight(d.h + (d.y - e.clientY));
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
   * [v0.44.5] @候选浮层定位。[v1.0.37.3] 锚点从 @ 按钮改为**光标位置**
   * （view.coordsAtPos）——tag 弹层跟随输入光标，而不是固定在按钮旁。
   */
  const positionMentionPicker = useCallback((): void => {
    const box = composerBoxRef.current;
    const picker = mentionPickerRef.current;
    if (!box || !picker) return;

    const boxRect = box.getBoundingClientRect();
    let trigger: { top: number; bottom: number; left: number } | null = null;
    const ed = editorRef.current;
    if (ed) {
      try {
        const coords = ed.view.coordsAtPos(ed.state.selection.$from.pos);
        if (coords && coords.left >= 0 && coords.top >= 0) {
          trigger = {
            top: coords.top,
            bottom: coords.bottom,
            left: coords.left,
          };
        }
      } catch {
        trigger = null;
      }
    }
    if (!trigger && mentionButtonRef.current) {
      const r = mentionButtonRef.current.getBoundingClientRect();
      trigger = { top: r.top, bottom: r.bottom, left: r.left };
    }
    if (!trigger) return;
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
    const spaceAbove = Math.max(0, trigger.top - visibleTop - MENTION_PICKER_GAP);
    const spaceBelow = Math.max(0, visibleBottom - trigger.bottom - MENTION_PICKER_GAP);
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
    const viewportLeft = Math.min(Math.max(trigger.left, visibleLeft), maxViewportLeft);
    const viewportTop = placement === 'top'
      ? trigger.top - MENTION_PICKER_GAP - renderedHeight
      : trigger.bottom + MENTION_PICKER_GAP;

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

  // [v1.0.37.3 R3] 输入框右键 → Electron 原生编辑菜单（剪切/复制/粘贴/全选）。
  // [v1.0.37.3 fix] 传应用语言给主进程（role 菜单不跟随系统语言，实测显示英文）。
  const handleContextMenu = (e: React.MouseEvent): void => {
    const bridge = (window as unknown as { knowe?: { showEditMenu?: (lang?: 'zh' | 'en') => void } }).knowe;
    if (bridge?.showEditMenu) {
      e.preventDefault();
      bridge.showEditMenu(i18n.language?.toLowerCase().startsWith('zh') ? 'zh' : 'en');
    }
    // 无桥（浏览器兜底）→ 不 preventDefault，让浏览器原生菜单工作。
  };

  return (
    <div
      ref={composerRef}
      className={'composer-wrap' + (expanded ? ' expanded' : '')}
      /* [v0.40.0] 多选模式：输入区半透明不可用 */
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

      {/* [v0.40.0] 引用条（右键「引用」）。 */}
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

      {/* [v1.0.19.4] 文件放置区域：输入框上方新展开的空间。 */}
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

        {/* [v1.0.37.3] textarea → TipTap 富文本（EditorContent）。右键走原生编辑菜单。 */}
        <div
          ref={editorElRef}
          className="tiptap-wrap"
          onContextMenu={handleContextMenu}
        >
          <EditorContent editor={editor} />
        </div>

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
