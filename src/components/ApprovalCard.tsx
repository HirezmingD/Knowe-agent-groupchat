/**
 * ApprovalCard.tsx — 审批卡（component-tree §D · ApprovalCard）
 *
 * DOM：.approval.enter-soft(.settled)
 *        > (.ap-head > 图标 + span.ap-label [+ span.ap-count])
 *        + [.ap-note]
 *        + 团队：(.ap-rows > (.ap-row > .avatar + span.nm + span.rl)×N)
 *          任务：(.ap-row > .avatar + span.nm + span.rl) + .ap-task
 *        + 未决：(.ap-actions > button.btn.btn-primary + button.btn.btn-ghost) + .ap-progress
 *          已决：.ap-resolved-bar(.muted)
 *
 * 铁律：
 *   1. 倒计时以 card.expires_at 为准（服务端时钟），不是本地起算。
 *   2. 四终态 confirmed / rejected / timeout / cancelled，**首个解决为准**：
 *      状态翻转只认服务端的 approval_resolved（applyEvent 已幂等），
 *      本地点了按钮只做「立即禁用 + 等待中」，绝不自己改状态。
 *      —— 这样点了「确认」而服务端判了 timeout 时，屏幕显示的是真相。
 *   3. 倒计时归零：按钮禁用、计数显示「已超时」，但状态仍等服务端事件落定。
 *
 * .ap-progress 的宽度是随时间变的，只能用 imperative 的 element.style
 * （ref 直改），不是 JSX style={{}}——铁律 1 禁的是后者。
 */

import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useKnoweStore } from '../store/store';
import { roleLabel, memberNameLabel } from '../shared/roleLabel';
import type { ApprovalItem, Member } from '../store/state';
import type { ApprovalCardData } from '../contract/envelope';
import { Avatar, palOf, glyphOf } from './Avatar';
import { ThinkingDot } from './ReasoningPanel';
import { useSessionActive } from './sessionActiveContext';
import { pickAvatar } from '../store/avatar';
import { projectIdForCard } from '../store/platform';
import { IconUsers, IconTask, IconCheck, IconX, IconPlus } from './icons';
import Markdown from './markdown';
import DirectoryPicker from './DirectoryPicker';
import RolePicker from './RolePicker';

// ── card 取字段（团队卡 / 任务卡两种形状） ──
// [v0.10a Issue 1] name：后端在提议时就算好的名字（envelope 的 proposed.name），卡上直接显示它
interface ProposedAgent { id: string; role: string; name?: string }

function proposedOf(card: ApprovalCardData): ProposedAgent[] {
  const c = card as unknown as { proposed?: ProposedAgent[] };
  return Array.isArray(c.proposed) ? c.proposed : [];
}
function targetOf(
  card: ApprovalCardData,
): { targetId: string; instruction: string; note: string; feedbackHistory: string[] } | null {
  const c = card as unknown as {
    target_id?: string; instruction?: string; note?: string; feedback_history?: string[];
  };
  if (!c.target_id) return null;
  // [v0.28] note = 项目经理就这次派活想对用户说的话。**这是他唯一能说的地方**——
  //   回复正文里的派活相关文字会被引擎摘掉（engine._strip_dispatch_echo）。
  //   所以这里不渲染 = 他以为自己说了、用户什么都没看到 = 造一个新的谎。**必须渲染。**
  return {
    targetId: c.target_id,
    instruction: c.instruction ?? '',
    note: (c.note ?? '').trim(),
    // [v1.0.24.3] 审批期间被用户改过的意见原文（后端 adjust_instruction 累积进卡体）。
    feedbackHistory: Array.isArray(c.feedback_history) ? c.feedback_history : [],
  };
}
/** [v0.9b] 移除卡：只有 target_id（+ 可选 reason），没有 instruction */
function removalOf(card: ApprovalCardData): { targetId: string; reason: string } | null {
  const c = card as unknown as { target_id?: string; reason?: string };
  if (!c.target_id) return null;
  return { targetId: c.target_id, reason: c.reason ?? '' };
}

function faceOf(
  members: Member[], id: string, fallbackRole: string, projectId: string,
  preferredName?: string,
) {
  const m = members.find((x) => x.id === id);
  if (m) {
    return {
      name: memberNameLabel(m.id, m.display.name), role: roleLabel(m.display.role),
      glyph: m.display.glyph, pal: m.display.pal, avatarUrl: m.display.avatarUrl,
    };
  }
  /*
   * 还没入驻的提议成员：头像按种子派生 —— 和他入驻之后的那张脸**必须是同一张**。
   *
   * [v0.8e #7] ★ 种子里要带 projectId。
   *   头像算法改成了「项目 + agent 一起做种」（不同项目的 fe_1 不再撞脸）——
   *   这里若还只传 id，卡上那张脸就是「空项目的 fe_1」，
   *   而他一进群，花名册走的是 faceFor(id, 项目)：**两张脸对不上**。
   *   这是 v0.5 修过一次的老伤，别再犯。
   *
   * [v0.10a Issue 1 红线] ★ 名字**优先用 preferredName**（后端算好的名字），绝不拿 id 当名字。
   *   正常路径上这个成员在 approval_card 事件里已经注册过（带名字），上面的 m 分支就命中了；
   *   这里是纯兜底 —— 万一没命中，也得显示名字，而不是把 fe_1 甩到用户脸上。
   */
  const nm = preferredName || id;
  return {
    name: nm, role: fallbackRole, glyph: glyphOf(nm), pal: palOf(id),
    avatarUrl: pickAvatar(id, projectId),
  };
}

/** [v0.5] 建群卡：知知提议的项目名（用户可以改） */
function projectNameOf(card: ApprovalCardData): string | null {
  const c = card as unknown as { project_name?: string };
  return typeof c.project_name === 'string' ? c.project_name : null;
}

/** 秒 → M:SS */
function fmt(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

export interface ApprovalCardProps {
  cardId: string;
  projectId: string;
  tool: string;                       // 'team' | 'task' | 'remove'（state.ts 已归一）
  card: ApprovalCardData;
  state: ApprovalItem['state'];
  expiresAt: string;                  // ISO 8601
  members: Member[];
  /**
   * [v0.30 Bug2/3] 这张卡收到过几次 approval_card 事件（state.ts 维护）。
   * 它是「我有新意见」的确定性回执通道：后端成功（指令换了）与失败
   * （空补丁重播，一字未变）都会让它 +1。sent 态的转圈只认它，
   * 不再靠 55 秒超时干等。
   */
  rev?: number;
}

/**
 * [v0.26] 「主管正在调整任务指令…」最多转多久。
 *
 * 比后端那次 LLM 调用的超时（KNOWE_ADJUST_TIMEOUT，默认 40s）多留一点余量：
 * 正常情况下**后端先超时并发一条 error**，用户看到的是一句人话；
 * 这里只兜「连 error 都没回来」（进程没了 / 连接断了）那种情况——
 * 兜底的意义是**不让他对着一张永远转圈的卡**，不是替后端报错。
 */
const FEEDBACK_TIMEOUT_MS = 55_000;

/**
 * [v1.0.23.14] ★ 整体 React.memo：切群/消息流重渲染时，若卡 props（card/state/
 *   expiresAt/members 等）引用未变则整卡跳过——这是「新群秒切、发卡后切过去卡 1 秒」
 *   的根治：卡片的 Markdown/DOM 是全消息流里最重的渲染单元，禁不起 ChatStream
 *   每次重渲染都跟着全量重跑。members 引用已在 selectors 层缓存稳定，card 由
 *   immer 管理（真变化才换引用），浅比较足够可靠。
 */
export const ApprovalCard: React.FC<ApprovalCardProps> = React.memo(({
  cardId, projectId, tool, card, state, expiresAt, members, rev,
}) => {
  const { t } = useTranslation();
  const approve = useKnoweStore((s) => s.approve);
  const reject = useKnoweStore((s) => s.reject);
  const createProject = useKnoweStore((s) => s.createProject);
  const feedbackInstruction = useKnoweStore((s) => s.feedbackInstruction);

  /*
   * ── [v0.24 #4] 「我有新意见」 ────────────────────────────────
   *
   *   以前用户对卡上的指令不满意，只有一条路：拒绝 → 重新打一遍需求 → 等新卡。
   *   来回三次就烦了。现在可以直接在卡上说「这里改一下」。
   *
   *   三态，够用，不多：
   *     idle    → 三个按钮：我有新意见 / 确认 / 拒绝
   *     writing → 卡片长出输入框，按钮变 发送 / 取消
   *     sent    → 收起输入框，转圈 +「主管正在调整任务指令…」
   */
  const [feedbackMode, setFeedbackMode] = useState<'idle' | 'writing' | 'sent'>('idle');
  const [feedbackText, setFeedbackText] = useState('');
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // 展开就把光标放进去 —— 让用户少点一下。
  useEffect(() => {
    if (feedbackMode === 'writing') taRef.current?.focus();
  }, [feedbackMode]);

  /*
   * 发送新意见 —— [v0.26] 走**控制面**，和 approve / reject 并列。
   *
   * ## v0.24 / v0.25 为什么两次都失败
   *
   *   两版都把意见包成一条**聊天消息**：sendMessage → engine.submit() → 作废旧卡
   *   → 项目经理重开一个回合 → 重新 propose_next。两次的结果一样：**旧指令原样重发**。
   *
   *   v0.25 我把锅算在「传输方式」上，于是拼了一段更凶的 prompt 塞给项目经理。还是没用。
   *   所以那个归因是错的。真正的原因是：
   *
   *   ★ 我们让项目经理**在一个完整的 agent 回合里**做这件事。而那个回合里：
   *       · 有一条刚被作废搞炸的 tool_call，在冲它喊「原样重试」
   *       · 有几万字上下文，用户那句意见只是其中一行
   *       · 它有十几个工具可以调、无数种话可以说
   *     「按这条意见改一版指令」在那里只是**众多选项之一**。
   *     它选错不是因为笨，是因为**有得选**。
   *
   * ## 这一版
   *
   *   feedback_instruction 直达 gate：找到那张还挂着的卡 → 一次**定向的、一次性的**
   *   模型调用（就这份指令 + 就这条意见 → 输出改好的指令，没有工具、没有历史）
   *   → 原地改卡面 → 重发同 card_id 的 approval_card。
   *
   *   **它不会分心，因为没有东西可以让它分心。**
   *   闸门的 future 碰都不碰 —— 卡还在等，倒计时照走，approve/reject 语义一个字没变。
   */
  const submitFeedback = () => {
    const text = feedbackText.trim();
    if (!text) return;
    setFeedbackMode('sent');
    feedbackInstruction(cardId, projectId, text);
  };

  /*
   * [v0.26 → v0.30 Bug2/3] 新指令回来了 / 后端答复了 → 收起转圈。
   *
   * ## v0.26 的判据为什么不够
   *
   *   老判据只有一条：「卡上的指令**变了** → 成功」。成功那半没问题；
   *   **失败那半是个洞**：后端返回「指令没有变化」时只发一条 error，
   *   卡面一个字不变 → 这个 effect 永远不醒 → 转圈干等 55 秒超时。
   *   用户看到的就是 Bug 3 第一幕：「系统提示调整失败 + 转圈卡死」。
   *
   * ## 这一版：rev 是**每一次点击的确定回执**
   *
   *   后端在 adjust_instruction 里保证：无论成败，恰好一次同 card_id 的
   *   approval_card 重播（成功带新指令；失败空补丁、卡面原样）。
   *   state.ts 每收到一条就把 rev +1。于是这里只需要盯 rev：
   *
   *     rev 动了 + 指令换了 → 成功：清空输入、回 idle（原地 morph 已完成）
   *     rev 动了 + 指令没换 → 失败：回 writing，**意见原样留在输入框里**——
   *       报错原因在会话流里那条红字上，用户改一改直接再发，不用重打。
   *
   *   55 秒超时降级为最后的兜底（进程没了 / 连接断了才会走到）。
   */
  const sentBaselineRef = useRef<{ rev: number; instruction: string } | null>(null);
  useEffect(() => {
    if (feedbackMode !== 'sent') {
      sentBaselineRef.current = null;
      return;
    }
    const nowRev = rev ?? 0;
    const nowInstruction = targetOf(card)?.instruction ?? '';
    if (sentBaselineRef.current === null) {
      // 记下发送那一刻的 rev + 指令，等回执。
      sentBaselineRef.current = { rev: nowRev, instruction: nowInstruction };
      return;
    }
    const base = sentBaselineRef.current;
    if (nowRev === base.rev && nowInstruction === base.instruction) return;   // 还没回执

    sentBaselineRef.current = null;
    if (nowInstruction !== base.instruction) {
      // 指令换了 → 成了（rev 未接线的旧数据也能靠这半边收起转圈）
      setFeedbackText('');
      setFeedbackMode('idle');
    } else {
      // rev 动了、指令没动 → 后端明确说「没改成」。退回输入态，意见留着。
      setFeedbackMode('writing');
    }
  }, [card, rev, feedbackMode]);

  // 兜底：后端挂了 / 连接断了 —— 别让他对着转圈的卡干等。
  useEffect(() => {
    if (feedbackMode !== 'sent') return;
    const t = setTimeout(() => {
      sentBaselineRef.current = null;
      setFeedbackMode('idle');               // 意见留在输入框里，他可以直接再发一次
    }, FEEDBACK_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [feedbackMode]);

  const proposedName = projectNameOf(card);
  const isProject = tool === 'create_project' || proposedName !== null;
  const [name, setName] = useState(proposedName ?? '');
  const [dir, setDir] = useState('');   // [v0.7 A0] 建群卡上的项目目录
  /** [主动拉入worker] 建群卡上勾选的职能前缀（最多 8 个）；空 = 不选，行为与旧版一致。 */
  const [roles, setRoles] = useState<string[]>([]);

  /*
   * [v0.8c #4a] 用户按过「确认」了吗？
   *
   *   跟 NewProjectModal 一样的规矩（v0.8b #6）：
   *   **报错是对「你刚才那一下不对」的回应，不是欢迎语。**
   *   卡片刚弹出来时安安静静；他按了确认、而名字或目录还空着，才亮红。
   *
   *   顺带把「确认」从 disabled 改成可点——灰键什么也不说，用户只能对着它猜自己
   *   漏了什么（这正是这条 bug 的现场：点了没反应，也没有任何提示）。
   */
  const [attempted, setAttempted] = useState(false);

  const pending = state === 'pending';
  const [submitted, setSubmitted] = useState(false);   // 点过按钮 → 立即禁用，防双击

  /*
   * [v1.0.23.14] ★ 倒计时拆出 parent，整卡不再每秒重渲染：
   *   · 倒计时文本 → CountdownLabel（memo 小组件，只重渲染自己那个 span）；
   *   · 进度条 → CountdownBar（纯 ref 直改宽度，零 setState，永不重渲染）；
   *   · parent 只在**超时那一瞬间** setTimedOut(true) 一次（按钮禁用），
   *     不再每秒 setRemain → 整卡（含 Markdown、DOM、拖拽中的 taskH）零无谓开销。
   *     这也修了拖拽卡：旧实现每秒整卡重渲染，React 用旧 taskH 覆盖拖拽中的
   *     DOM maxHeight → 拖一下跳回一下 = 用户看到的「卡、延迟大」。
   */
  const [timedOut, setTimedOut] = useState(false);
  useEffect(() => {
    if (!pending || !expiresAt) { setTimedOut(false); return; }
    const ms = new Date(expiresAt).getTime() - Date.now();
    if (ms <= 0) { setTimedOut(true); return; }
    const t = setTimeout(() => setTimedOut(true), ms);
    return () => clearTimeout(t);
  }, [pending, expiresAt]);

  /*
   * [v1.0.23.12] 派活卡指令框（.ap-task-md）下边界可上下拉伸。
   *   鼠标移到下边界手柄 → 高亮 + ns-resize 光标；按住拖动改高度。
   *   ★ 拖拽期间**直改 DOM style**（铁律：ref 直改，不 setState）——setState 会触发
   *     整卡 React 重渲染 + Markdown 重新解析，每帧一次就是卡死的根源。
   *   拖拽结束才把最终高度写进 state（仅用于 JSX 渲染时还原）。
   *
   *   [v1.0.23.5] 拖拽改 **height** 而非 max-height：
   *     max-height 的渲染高度 = min(内容, max-height)——指令内容不满 220px 时
   *     向下拉 max-height 纹丝不动（物理上没东西可撑），表现为「不跟鼠标」。
   *     height 模式严格跟随鼠标，内容多则滚动、少则留白（文本域拉伸标准行为）。
   */
  const taskBoxRef = useRef<HTMLDivElement>(null);
  const [taskH, setTaskH] = useState<number | null>(null);          // null = 用 CSS 默认 220px（max-height 自动模式）
  const [resizing, setResizing] = useState(false);
  const taskDragRef = useRef<{ startY: number; startH: number } | null>(null);

  const startTaskResize = (e: React.MouseEvent): void => {
    e.preventDefault();                       // 防止拖拽中选中文本
    const el = taskBoxRef.current;
    if (!el) return;
    const startH = taskH ?? el.clientHeight;
    taskDragRef.current = { startY: e.clientY, startH };
    setResizing(true);
    const onMove = (ev: MouseEvent): void => {
      const d = taskDragRef.current;
      if (!d) return;
      const h = Math.max(96, Math.min(560, d.startH + (ev.clientY - d.startY)));
      // 直改 DOM：零 React 重渲染，Markdown 不重解析 → 丝滑；height 模式严格跟手
      el.style.height = `${h}px`;
      el.style.maxHeight = 'none';
    };
    const onUp = (): void => {
      taskDragRef.current = null;
      setResizing(false);
      // 拖完把最终值落回 state：JSX 渲染（如指令更新重挂）时能还原用户拉的高度
      const finalH = el.style.height ? parseFloat(el.style.height) : null;
      setTaskH(finalH !== null && Number.isFinite(finalH) ? finalH : null);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const isTeam = tool === 'team';
  /**
   * [v0.9b] 移除成员卡。
   *
   * 它长得像派活卡（都只有一个 target_id），但**要说的是相反的一件事**——
   * 所以头、图标、说明、按钮文案全都要换。用一张「派发任务」的壳去承载
   * 「要不要把 fe_1 撤掉」，是在骗屏幕前的人点头。
   */
  const isRemove = tool === 'remove';
  const removal = isRemove ? removalOf(card) : null;

  /**
   * [v0.5 #9] 确认建群。
   *
   * 项目名用户可能改过，而 approve() 只发 {approval_id, project_id}，带不了名字。
   * 所以走既有的 create_project 指令：先用（可能改过的）名字把项目建出来，
   * 再 approve 把卡落定。
   *
   * projectIdForCard(cardId) 直接返回稳定的 canonical project_id。乐观会话、出站指令和
   * 后端项目从第一帧起就是同一个身份，不再依赖异步 alias 重键来消灭临时群。
   */
  const nameBad = isProject && attempted && !name.trim();
  const dirBad = isProject && attempted && !dir.trim();

  const confirmProject = (): void => {
    const finalName = name.trim();
    const finalDir = dir.trim();
    if (!finalName || !finalDir) {
      setAttempted(true);              // ← 现在才有资格标红
      return;                          // ★ 不 approve、不 setSubmitted：卡还开着，他还能改
    }
    setSubmitted(true);
    createProject(projectIdForCard(cardId), finalName, finalDir, cardId, roles);
    approve(cardId, projectId);
  };
  const target = targetOf(card);

  /*
   * [v0.24 #4] 「我有新意见」只对**派活卡**开放。
   *
   *   建群卡：卡上本来就能改名字、挑目录 —— 用户已经能直接编辑，不需要再跟项目经理商量。
   *   移除卡：只有「移」和「不移」两种答案，没有中间地带可提意见。
   *   派活卡：指令是模型写的一大段文字，用户想微调是**常态**——这里才需要。
   *
   *   给一个用不上的按钮，比不给更糟：它会让用户以为点了会有事发生。
   */
  const isTask = !!target && !isProject && !isRemove;
  const rows = proposedOf(card);

  return (
    <div className={'approval enter-soft' + (pending ? '' : ' settled')} data-ap={cardId}>
      {/* ── 头 ── */}
      <div className="ap-head">
        {isProject ? <IconPlus /> : isTeam ? <IconUsers /> : isRemove ? <IconX /> : <IconTask />}
        <span className="ap-label">
          {isProject ? t('approval.card.07')
            : isTeam ? t('approval.card.20')
              : isRemove ? t('approval.card.19')
                : t('approval.card.14')}
        </span>
        {pending && (
          <CountdownLabel expiresAt={expiresAt} />
        )}
      </div>

      <div className="ap-note">
        {isProject
          // [v0.7b #1] 卡上得把目录这件事说出来 —— 原来只说「名字可以改」，
          //   用户压根没注意到下面还要选个目录，对着灰掉的「确认」键干瞪眼。
          ? t('approval.card.17')
          : isTeam
            ? t('approval.card.22')
            : isRemove
              // [v0.9b] 把后果说清楚：这不是删除，他的东西都还在。
              //   用户对「移除」最大的恐惧是「我的东西会不会跟着没了」——先回答这个。
              ? t('approval.card.16')
              : t('approval.card.21')}
      </div>

      {/* ── 主体 ── */}
      {isProject ? (
        <>
        <div className="ap-project">
          <label className="ap-project-label" htmlFor={`apn-${cardId}`}>{t('common.23')}</label>
          <input
            id={`apn-${cardId}`}
            className={'ap-project-input' + (nameBad ? ' needs-pick' : '')}
            type="text"
            value={name}
            disabled={!pending || submitted || timedOut}
            maxLength={40}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && pending && !submitted && !timedOut) confirmProject();
            }}
          />
          {nameBad && <div className="dir-hint" role="alert">{t('approval.card.04')}</div>}
        </div>
        <div className="ap-project">
          <label className="ap-project-label">{t('common.24')}</label>
          {/* [v0.8c #4b] 路径框在左、「选择目录」在右（CSS 的 .approval .dir-row 改成横排了） */}
          <DirectoryPicker
            value={dir}
            onChange={setDir}
            label={t('common.04')}
            required
            showError={dirBad}          /* [v0.8c #4a] 按过确认、还空着 → 这才标红 */
          />
        </div>
        {/* [主动拉入worker] 建群卡上选择职能：点「确认」→ 建群 + 拉人一步完成（可不选） */}
        <RolePicker
          selected={roles}
          onChange={setRoles}
          disabled={!pending || submitted || timedOut}
        />
        </>
      ) : isTeam ? (
        <div className="ap-rows">
          {rows.map((a) => {
            const f = faceOf(members, a.id, a.role, projectId, a.name);
            return (
              <div className="ap-row" key={a.id}>
                <Avatar glyph={f.glyph} pal={f.pal} size={36} src={f.avatarUrl} />
                <span className="nm">{f.name}</span>
                <span className="rl">{f.role}</span>
              </div>
            );
          })}
        </div>
      ) : isRemove && removal ? (
        <>
          <div className="ap-row">
            {(() => {
              const f = faceOf(members, removal.targetId, t('common.07'), projectId);
              return (
                <>
                  <Avatar glyph={f.glyph} pal={f.pal} size={36} src={f.avatarUrl} />
                  <span className="nm">{f.name}</span>
                  <span className="rl">{f.role}</span>
                </>
              );
            })()}
          </div>
          {removal.reason && <div className="ap-task">{t('approval.card.reason', { reason: removal.reason })}</div>}
        </>
      ) : target ? (
        <>
          <div className="ap-row">
            {(() => {
              const f = faceOf(members, target.targetId, t('common.07'), projectId);
              return (
                <>
                  <Avatar glyph={f.glyph} pal={f.pal} size={36} src={f.avatarUrl} />
                  <span className="nm">{f.name}</span>
                  <span className="rl">{f.role}</span>
                </>
              );
            })()}
          </div>
          {/*
            [v0.24 #3] 指令是**模型写的**，它天生带 markdown：**粗体**、- 列表、1. 编号。
            以前原样当纯文本铺出来，符号和正文糊成一团，卡上像贴了一段源码。
            这里复用气泡那套 Markdown 组件（react-markdown，默认不放行原始 HTML —— 
            指令是模型生成的，等于半个不可信输入，这条底线不能松）。
            长指令由 CSS 限高 + 滚动条兜住，不撑爆卡片。
          */}
          {/* [v1.0.23.12] ref + 拖拽高度：taskH=null 走 CSS 默认 220px（max-height 自动模式）；拖过则用用户拉的固定高度 */}
          <div className="ap-task-wrap">
            <div
              ref={taskBoxRef}
              className="ap-task ap-task-md"
              style={taskH !== null ? { height: taskH, maxHeight: 'none' } : undefined}
            >
              <Markdown text={target.instruction} />
            </div>
            {/* [v1.0.24.3] 这张卡在审批期间被用户【我有新意见】改过 → 右上角小标记。
                用户确认后（卡已落定）这里不再渲染，PM 的收尾发言靠后端回执对齐新指令。 */}
            {pending && target.feedbackHistory.length > 0 && (
              <span className="ap-amended-badge">{t('approval.card.amended')}</span>
            )}
            {/* [v1.0.23.12] 下边界拉伸手柄：hover 高亮 + 按住上下拖 */}
            <div
              className={'ap-task-resize' + (resizing ? ' dragging' : '')}
              onMouseDown={startTaskResize}
            />
          </div>

          {/*
            [v0.28] 项目经理的话，就在指令下面。
            
            ★ 为什么它在卡上而不在气泡里：以前「想对用户说一句关于这次派活的话」有两条路
              ——写进回复正文（便宜），或者调工具（贵）。模型永远走便宜那条，
              于是说了却没调。现在只剩这一条：**要说，就得穿过工具调用**。
              这就是「把嘴焊在手上」的字面实现。

            没填就不渲染 —— 大多数派活是不需要附言的，空框子只会让卡变胖。
          */}
          {target.note && (
            <div className="ap-note-say">
              <span className="ap-note-say-tag">{t('approval.card.06')}</span>
              <span className="ap-note-say-text">{target.note}</span>
            </div>
          )}
        </>
      ) : null}

      {/* ── 未决：操作 + 进度线 ── */}
      {pending ? (
        <>
          {/*
            [v0.24 #4] 三个按钮等宽等高。「我有新意见」排在最左 ——
            它是**最轻的一个动作**（既不点头也不摇头，只是想聊聊），
            放在离「确认」最远的地方，误触的代价最小。
          */}
          <div className={'ap-actions' + (isTask ? ' ap-actions-3' : '')}>
            {isTask && feedbackMode === 'idle' && (
              <button
                className="btn btn-ghost ap-btn-say"
                disabled={submitted || timedOut}
                onClick={() => setFeedbackMode('writing')}
              >
                {t('approval.card.newOpinion')}
              </button>
            )}

            {feedbackMode === 'writing' ? (
              <>
                <button
                  className="btn btn-primary"
                  disabled={!feedbackText.trim()}
                  onClick={submitFeedback}
                >
                  {t('composer.04')}
                </button>
                <button
                  className="btn btn-ghost"
                  onClick={() => { setFeedbackMode('idle'); setFeedbackText(''); }}
                >
                  {t('chat.stream.03')}
                </button>
              </>
            ) : feedbackMode === 'sent' ? (
              /* 收起输入框、转圈、说人话。这一步用户什么都不用做，只要等。 */
              <div className="ap-adjusting" aria-live="polite">
                {/* [2026-08-08] 转圈 ap-spinner → ThinkingDot（morphing-infinity SVG，与推理指示器统一；
                     SMIL 动画不受 CSS animation 禁用影响，根治「转圈不动」） */}
                <ThinkingDot />
                <span>{t('approval.card.05')}</span>
              </div>
            ) : (
              <>
                <button
                  className="btn btn-primary"
                  /* [v0.8c #4a] 只有「已提交」和「已超时」才禁用。
                     名字/目录没填不禁用——按下去会精确告诉他缺什么，比一颗灰键有用。 */
                  disabled={submitted || timedOut}
                  onClick={() => {
                    if (isProject) { confirmProject(); return; }
                    setSubmitted(true);
                    approve(cardId, projectId);
                  }}
                >
                  {/* [v0.9b] 移除卡上按钮写「确认移除」——一个孤零零的「确认」，
                      在一张要把人撤掉的卡上，说得太轻了。 */}
                  {submitted ? t('approval.card.09') : isRemove ? t('approval.card.18') : t('common.20')}
                </button>
                <button
                  className="btn btn-ghost"
                  disabled={submitted || timedOut}
                  onClick={() => { setSubmitted(true); reject(cardId, projectId); }}
                >
                  {isRemove ? t('approval.card.15') : t('approval.card.13')}
                </button>
              </>
            )}
          </div>

          {/*
            输入区用 grid-template-rows: 0fr → 1fr 做展开。
            为什么不是 max-height：max-height 得先猜一个够大的值，猜大了收起来会「先愣一下再动」，
            猜小了长文本被切掉。0fr→1fr 是**真的按内容高度**过渡的，一行 CSS，不用 JS 量高度。
            —— 用户要的是「长出来」，不是「跳出来」。
          */}
          <div className={'ap-feedback' + (feedbackMode === 'writing' ? ' open' : '')}>
            <div className="ap-feedback-inner">
              <textarea
                ref={taRef}
                className="ap-feedback-ta"
                placeholder={t('approval.card.12')}
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
                onKeyDown={(e) => {
                  // Ctrl/⌘+Enter 发送；Esc 收起。回车留给换行——这是多行输入框。
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitFeedback(); }
                  if (e.key === 'Escape') { setFeedbackMode('idle'); setFeedbackText(''); }
                }}
              />
            </div>
          </div>

          <CountdownBar expiresAt={expiresAt} />
        </>
      ) : (
        <ResolvedBar state={state} />
      )}
    </div>
  );
});

// ═══════════════════════════════════════════════════════════════
// [v1.0.23.14] 倒计时两件套：拆出 parent，整卡不再每秒重渲染
// ═══════════════════════════════════════════════════════════════

/**
 * 倒计时文本（ap-count）。memo 小组件：自己每秒 tick、只重渲染自己那个 span。
 * 超时显示「已超时」（approval.card.03），未超时显示「剩余 M:SS」。
 */
const CountdownLabel: React.FC<{ expiresAt: string }> = React.memo(({ expiresAt }) => {
  const { t } = useTranslation();
  const active = useSessionActive();
  const [remain, setRemain] = useState<number | null>(null);

  useEffect(() => {
    // [v1.0.24.6-P0] 隐藏会话停摆：不跑倒计时（恢复时重新计算剩余时间）
    if (!active) return;
    const deadline = new Date(expiresAt).getTime();
    if (Number.isNaN(deadline)) { setRemain(null); return; }
    const tick = (): void => setRemain((deadline - Date.now()) / 1000);
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [expiresAt, active]);

  if (remain === null) return null;
  return (
    <span className="ap-count">
      {remain <= 0 ? t('approval.card.03') : t('approval.card.01') + ' ' + fmt(remain)}
    </span>
  );
});

/**
 * 倒计时进度条（ap-progress）。纯 ref 直改宽度（铁律：imperative element.style），
 * 零 setState → 永不触发 React 重渲染。总时长以首次 tick 时的剩余为准。
 */
const CountdownBar: React.FC<{ expiresAt: string }> = React.memo(({ expiresAt }) => {
  const barRef = useRef<HTMLDivElement>(null);
  const totalRef = useRef<number | null>(null);
  const active = useSessionActive();

  useEffect(() => {
    // [v1.0.24.6-P0] 隐藏会话停摆：不跑进度条（恢复时从当前剩余重新起）
    if (!active) return;
    const deadline = new Date(expiresAt).getTime();
    if (Number.isNaN(deadline)) return;
    const tick = (): void => {
      const left = (deadline - Date.now()) / 1000;
      if (totalRef.current === null) totalRef.current = Math.max(left, 1);
      const bar = barRef.current;
      if (bar && totalRef.current) {
        const pct = Math.max(0, Math.min(100, (left / totalRef.current) * 100));
        // [v1.0.24.6-P3] width → transform scaleX：宽度写触发 layout/reflow，
        //   transform 走合成器线程（GPU），进度条每秒更新不再碰主线程布局。
        //   origin-left 由 CSS 提供（.ap-progress 加 transform-origin）。
        bar.style.transform = `scaleX(${pct / 100})`;
      }
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [expiresAt, active]);

  return <div className="ap-progress" ref={barRef} />;
});

const RESOLVED_TEXT: Record<string, string> = {
  confirmed: 'approval.card.10',
  rejected: 'approval.card.08',
  timeout: 'approval.card.11',
  cancelled: 'approval.card.02',
};

const ResolvedBar: React.FC<{ state: ApprovalItem['state'] }> = ({ state }) => {
  const { t } = useTranslation();
  const ok = state === 'confirmed';
  return (
    <div className={'ap-resolved-bar' + (ok ? '' : ' muted')}>
      {ok ? <IconCheck /> : <IconX />}
      <span>{RESOLVED_TEXT[state] ? t(RESOLVED_TEXT[state]) : state}</span>
    </div>
  );
};

export default ApprovalCard;
