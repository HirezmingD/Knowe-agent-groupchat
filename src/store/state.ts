/** [v1.0.13][R3] Deterministic Completion projection and legacy authority hotfix. */
/**
 * state.ts — 纯状态突变函数（v2 · Claude 审计版）
 *
 * 按 Claude §3.3 新状态树重写。零 DOM 依赖，框架无关。
 * applyEvent 是全仓库唯一实现（grep 级 CI 检查）。
 *
 * 关键改动（v2）：
 *   - Item 联合类型重构（user 带 delivery 乐观渲染三态 / approval 用 timeout 替 expired）
 *   - applyEvent 新规则：乐观渲染 + 空 content 守卫 + 审批幂等 + 快照绕过水位
 *   - StateSnapshot 已纳入 InboundEventSchema（v2 envelope），不再需要独立类型
 *
 * version: 2
 */

import type { InboundEvent, ApprovalCardData, ProducedFile, AttachmentInput, ForwardedPayload, ActivityLedgerEntry } from '../contract/envelope';
import { normalizeApprovalCard } from '../contract/envelope';
import { featureEnabled } from '../shared/featureFlags';
import {
  normalizeCompletionVersion,
  shouldAcceptCompletionMetadata,
  shouldReplaceCompletionProjection,
  type CompletionAuthority,
} from './completionProjection';

/*
 * [v0.36] 边界铁律：components 只 import store/selectors，不碰 contract。
 *   文件卡片、预览面板要用到 ProducedFile 类型，所以从这里**再导出**一次，
 *   让它们从 store/state 取型（和它们取 Item / Member 同一处）。
 */
export type { ProducedFile, AttachmentInput } from '../contract/envelope';
import { faceFor } from './avatar';
import {
  appendObservableStage,
  appendToolActivity,
  finishWorkStages,
  type ObservableStageEvent,
  type ToolActivityLine,
  type WorkStageLine,
} from './toolActivity';
import i18n from '../i18n';
/*
 * [v0.9c] genPair() 不再被调用 —— 名字归后端管（见 registerMember）。
 *   import 一并撤掉：留着它，只会让下一个人以为「名字是这儿生成的」。
 *   nameGenerator.ts 本身**不删**（将来做「给成员起花名」还用得上）。
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
type FrictionFn = (category: string, message: string, details?: Record<string, unknown>) => void;
declare global {
  interface Window {
    recordFriction?: FrictionFn;
  }
}
/* eslint-enable @typescript-eslint/no-explicit-any */

// ═══════════════════════════════════════════════════════════════
// 一、类型定义（Claude §3.3 新状态树）
// ═══════════════════════════════════════════════════════════════

/** 连接状态（六态：含 handshaking） */
export type ConnStatus = 'connecting' | 'handshaking' | 'live' | 'resync' | 'reconnecting' | 'closed';

/** Agent 展示信息 */
export interface AgentDisplay {
  name: string;
  role: string;
  roleEn: string;
  glyph: string;
  pal: string;
  kind: string;
  avatarUrl?: string;
}

/** 成员 */
export interface Member {
  id: string;
  state: 'idle' | 'busy' | 'standby';
  /** 最近一次进入 busy 的时间序号（毫秒基准，后开始者更大）。idle 时清空。 */
  busySince?: number;
  /**
   * 当前仍未收到对称 agent_idle 的可见执行。
   *
   * key = channel + scope；同一 scope 的重复 active 不会刷新时间，迟到 idle 也只能
   * 删除自己的 key。这个集合是花名册 busy/idle 的唯一事实来源。
   */
  activeScopes?: Record<string, number>;
  display: AgentDisplay;
  /**
   * [v0.9b] 'removed' = 已归档：不再接新任务。
   *
   * ★ **人不从数组里删掉。** 他来过、干过活、交过报告——那些消息还在时间线上，
   *   气泡要认得出他是谁（头像、名字都从 members 里查）。把他从数组里抹掉，
   *   历史消息就会变成一堆没有脸的陌生人。
   *   所以：只打一个标记；宫格和人数把他滤掉，历史照旧。
   */
  status?: 'active' | 'removed';
}

// ── Item 联合类型（§3.3 新树） ──

/** 用户消息项 — 乐观渲染三态 */
export interface UserItem {
  kind: 'user';
  text: string;
  cmid: string;
  /** 乐观渲染状态：pending→confirmed（echo 到）/ suspect（超时） */
  delivery: 'pending' | 'confirmed' | 'suspect';
  /**
   * [v0.38] 这条消息的时间（毫秒）。来自事件 ts（eventMillis(ev)），回放历史时也带得上。
   * 时间分隔线据此判断相邻消息是否间隔 ≥ 4 分钟。可选：老事件没 ts 时退回接收时刻。
   */
  ts?: number;
  /** [v0.38.3 #3] 这条消息对应的事件 seq——「跳转到消息出处」按它把气泡标 data-seq。 */
  seq?: number;
  /**
   * [v0.40.1] 引用条（右键「引用」后发送）。渲染成气泡内顶部的 .qref（微信式引用块：
   * 被引用人名字 + 原文预览），点它可跳回原消息。**只影响本地显示**——发给后端的正文
   * 另做结构化拼装（见 Composer / store.sendMessage 的 opts）。
   */
  quote?: MessageQuote;
  /**
   * [v0.40.1] 转发带来的文件（图片/视频/文件卡片）。用户气泡原本只有文本；转发要「带原格式」，
   * 于是允许用户气泡也挂文件卡（渲染走既有 FileCardList）。
   */
  files?: ProducedFile[];
  /**
   * [v0.40.1] 转发标记：这条用户气泡其实是一条转发消息。
   *   sourceName 显示成「转发自 X」；markdown=true 时正文按 Markdown 渲染（转发自 Agent 的富文本）。
   *   [v1.0.23.1] 协议形状 = envelope.ForwardedPayload：主文案配言 comment、原文 originalText 进引用窗。
   */
  forwarded?: ForwardedPayload;
  /**
   * [v1.0.19.4] 这条用户消息带的本地附件（路径 + 身份 + 签名，无字节）。气泡下方渲染
   *   文件卡；点卡片用系统默认程序打开、被移动/删除时明确提示（DESIGN 决策 #3 / 验收 #6/#8）。
   */
  attachments?: AttachmentInput[];
}

/** [v0.40.1] 气泡内引用块（.qref）所需数据。 */
export interface MessageQuote {
  /** 被引用人显示名。 */
  name: string;
  /** 被引用原文预览（已截断）。 */
  text: string;
  /** 被引用消息的 itemKey——点 .qref 跳回原文用。 */
  ref?: string;
}

/** [v0.40.1] 转发消息的元信息（贴在转发生成的用户气泡上）。
 *  [v1.0.23.1] 统一使用协议类型 envelope.ForwardedPayload：
 *  气泡主文案是配言（comment），原文进引用窗（originalText），协议与展示同源不漂移。
 */
export type { ForwardedPayload as ForwardMeta } from '../contract/envelope';

/**
 * [v0.40.1] 一条「待转发内容」——转发弹窗按它把消息投进目标会话。
 * 单条消息转发 = 一个 ForwardItem；多选转发 = 多个；收藏卡转发 = 从收藏条目构造一个。
 */
export interface ForwardItem {
  text: string;
  files?: ProducedFile[];
  /** 目标气泡是否按 Markdown 渲染正文（源自 Agent 富文本 = true）。 */
  markdown: boolean;
  /** 源发送者显示名（渲染「转发自 X」）。 */
  sourceName: string;
  /** [v1.0.23.1] 来源群/项目名（LLM 模板的「{群/项目名}」+ 引用窗 header）。 */
  sourceProjectName?: string;
  /** [v1.0.23.1] 用户附言（转发时新输入，可为空）。 */
  comment?: string;
  sourceRef?: { projectId: string; itemKey?: string };
}

/**
 * [v0.23.1 问题五] 活动行的一条。
 *
 * `n` 是**连续同一个工具**的次数：连点五次 browser_click 应该是「正在点击 ×5」一行，
 * 不是五行一模一样的字。
 */
export type ActivityLine = ToolActivityLine;

/**
 * 临时态首帧保护。只存在于当前 renderer 内存，不进后端事件、不落任何持久化。
 * `settlePending` 表示权威终态已经到达，只等过程态至少 paint 一帧后切换视图。
 */
export interface TransientFrameGuard {
  id: string;
  painted: boolean;
  settlePending: boolean;
}

/** Agent 消息项 */
export interface AgentItem {
  kind: 'agent';
  agentId: string;
  /** 可见执行关联；临时气泡、工具活动、message/idle 都按这两个字段精确闭合。 */
  scopeId?: string;
  channelId?: string;
  text: string;
  streaming?: boolean;
  /** live-only：八阶段时间线（当前阶段 + 最近完成阶段）。 */
  stages?: WorkStageLine[];
  /** live-only：通用“秒建秒结”临时态首帧保护。 */
  transientFrame?: TransientFrameGuard;
  /** [v1.0.23.3] 刚由流式定格（morph 标记）：气泡入场用 morph 动画而非闪烁替换。
      仅实时流式落定设置；回放/快照重建的 item 无此标记。 */
  morphIn?: boolean;
  /**
   * [v0.23.1] 这一轮它都干了些什么（tool_gen 累出来的）。**逐条叠加**，不是覆盖。
   *
   * ★ 它和 `text` 是**两个东西**，这一点是 v0.23.1 的核心教训：
   *   v0.23 把模型的中间推理实时灌进了 `text` —— 而 `text` 正是最终答案要落的那个字段。
   *   两者共用一个格子，于是推理过程混进了正式消息（v0.23.1 问题四）。
   *   现在活动只走 tool_gen，**永远不碰 text**：正文只可能来自 message。
   *   「推理混进正文」从此在结构上不可能，不靠任何人记得清缓冲。
   *
   * 只在 streaming 期间有意义：气泡一落定（streaming=false）就换成 MessageBubble，
   * 整条活动栈跟着消失 —— 不需要额外清理（少一处清理 = 少一个忘记清理的 bug）。
   */
  activities?: ActivityLine[];
  /**
   * [v0.36] 这条消息定格时，本轮 Worker 产出的文件（后端随 message 事件捎来）。
   *
   * 它挂在**定格后的**气泡上（streaming=false 的那一刻由 message 事件写入），
   * 渲染层据此在气泡正下方画文件卡片。空/缺省 = 这条消息没产出文件（绝大多数消息）。
   *
   * ★ 有 files 的空文本气泡**不算空气泡**：Worker 写了文件却没说话时，卡片仍要挂得住
   *   （见 ChatStream / MessageBubble 的空气泡守卫，都放行「有 files」）。
   */
  files?: ProducedFile[];
  /**
   * [v0.38] 这条消息的时间（毫秒）。来自事件 ts（eventMillis(ev)），回放历史时也带得上。
   * 时间分隔线据此判断相邻消息是否间隔 ≥ 4 分钟。流式气泡在 stream_delta 起始时打上，
   * 定格后沿用；非流式 message 直接用 message 事件的 ts。
   */
  ts?: number;
  /** [v0.38.3 #3] 这条消息对应的事件 seq——用于「跳转到消息出处」。 */
  seq?: number;
  /** CompletionEvent 展示身份；重连/重放时按 completionId 原位合并。 */
  completionId?: string;
  completionStatus?: string;
  completionVersion?: number;
  /** Same-version authority: view > message > status, independent of arrival order. */
  completionAuthority?: CompletionAuthority;
  completionTerminal?: boolean;
  completionTransient?: boolean;
  completionGaps?: string[];
  completionNextActions?: string[];
  /**
   * [v1.0.23.3] 推理全文（reasoning_content 透传）。流式期间实时累积，
   * message 落定时以事件携带的权威值为准；旧消息无此字段 → 推理模块不渲染。
   */
  reasoning?: string;
  /** [v1.0.23.3] 思考耗时（秒）。随 message 落定；流式期间 undefined。 */
  reasoningSeconds?: number;
  /** [v1.0.24.4-r14] 派卡接力标记：approval_card 事件定格本推理气泡时置 true——
   *   推理面板保持展开（不折叠成小条），供收起动画从完整高度开始。
   *   仅内存标记（不落盘）；历史重放无此标记 → 历史行正常折叠。 */
  relayPending?: boolean;
  /**
   * [v1.0.23.3] 四方向建议卡片（辅助 LLM 提取，独立 suggestions 瞬时事件）。
   * 不落盘：纯内存，重启/刷新/历史回放自然消失。用户手动发新消息 → 清空（D-5）。
   */
  suggestions?: SuggestionItem[];
}

/** [v1.0.23.3] 四方向建议卡片项。 */
export interface SuggestionItem {
  title: string;
  sub: string;
}

/** 系统消息项 */
export interface SystemItem {
  kind: 'system';
  text: string;
  level: 'info' | 'error';
  completionId?: string;
  completionStatus?: string;
  completionVersion?: number;
  /** Same-version authority: view > message > status, independent of arrival order. */
  completionAuthority?: CompletionAuthority;
  completionTerminal?: boolean;
  completionTransient?: boolean;
  completionGaps?: string[];
  completionNextActions?: string[];
}

/** 审批卡项 — timeout 替代 expired */
export interface ApprovalItem {
  kind: 'approval';
  cardId: string;
  projectId: string;
  tool: string;
  card: ApprovalCardData;
  state: 'pending' | 'confirmed' | 'rejected' | 'cancelled' | 'timeout';
  expiresAt: string;
  recovered?: boolean;
  /**
   * [v0.30 Bug4] 这张卡是**谁**提议的（approval_card 事件里的 agent_id，一般是项目经理）。
   *
   * 用途只有一个：卡弹出/落定时，只定格**这个人**的流式气泡。
   * v0.28 的串行世界里「定格所有人」是对的——卡一弹，全项目都停在闸门上；
   * v0.29 之后 Worker 在后台跑，他的「正在工作」气泡和这张卡毫无关系，
   * 一并冻掉就是 Bug 4（工作中成员的状态气泡凭空消失）。
   */
  agentId?: string;
  /**
   * [v0.44.12] 这张组队卡为了渲染头像而**临时新建**进 c.members 的精确 id。
   *
   * 拒绝时只能删除这里记录的人，不能拿 card.proposed 去扫整个花名册：
   * proposed 里也可能是一个早已存在的归档成员（恢复提案），那条历史身份必须保留。
   */
  provisionalMemberIds?: string[];
  /**
   * [v0.30 Bug2/3] 这张卡收到过几次 approval_card 事件（含首次）。
   *
   * 它是「我有新意见」的**确定性回执通道**：后端无论调整成功（instruction 变了）
   * 还是失败（空补丁重播，一个字没变），都会重发一条同 card_id 的 approval_card
   * → rev +1。ApprovalCard 组件在 sent 态盯着它：rev 动了 + 指令换了 = 成功；
   * rev 动了 + 指令没换 = 失败 → 退回输入态。转圈不再靠 55 秒超时兜底。
   */
  rev?: number;
}

export type Item = UserItem | AgentItem | SystemItem | ApprovalItem;

/** 项目会话 */
export interface Conv {
  projectId: string;
  projectName?: string;
  items: Item[];
  members: Member[];
  banner: string | null;
  /**
   * [v0.7 #1] 这个会话的输入框草稿。
   *
   * 草稿是**属于会话的**，不属于输入框。原来 Composer 用自己的 useState 存文字，
   * 一个输入框服务所有项目——切到别的群，字还在那儿；发出去，发到的是新群。
   * 微信不是这样的：每个聊天的输入框各记各的。
   */
  draft: string;
  /**
   * [v0.8d #5] 没看过的消息有几条。
   *
   * 归会话所有（和草稿一样）：谁的消息谁记账。左栏的红点、任务栏的闪烁，
   * 读的都是这一个数——**一处记账，两处显示**，绝不会出现「红点没了但任务栏还在闪」。
   */
  unread: number;
  /**
   * [v0.7 A0] 项目目录（Worker 沙箱的根）。
   *
   * 新建项目时必填；后端拿它当 workspace_root。前端留一份只是为了显示，
   * 判定以后端为准。
   */
  projectDir?: string;
  /**
   * [v0.40.2 #7] 私聊会话所归属的父项目（群聊）。
   *
   *   私聊会话 id 形如 dm:{group}:{agent}，projectName 存的是**该成员的显示名**
   *   （「邓青恒」），不是群名。可从私聊界面收藏消息时，标题/来源名要用**群名**
   *   （「测试1 - 邓青恒 · 产品 - 发言」）——所以私聊会话得能追溯到它的父项目。
   *   进入私聊（enterDm）时把父群 id 和名字存这儿；收藏取 parentProjectName。
   *   群聊会话本身没有这两个字段（它就是自己的项目）。
   */
  parentProjectId?: string;
  parentProjectName?: string;
}

/** AGENTS 注册表 */
export type AgentRegistry = Record<string, AgentDisplay>;

/** ROLE_TYPES 条目 */
export interface RoleType {
  type: string;
  label: string;
  tpl: string;
}

/** 全局通知（无 project_id 的服务器级错误） */
export interface GlobalNotice {
  /** Stable id lets a long-running operation update one existing Toast. */
  id?: string;
  message: string;
  timestamp: string;
}

// ═══════════════════════════════════════════════════════════════
// 二、静态配置
// ═══════════════════════════════════════════════════════════════

/**
 * 默认 Agent 模板
 *
 * [v0.10a Issue 4] ★ 角色库扩到 24 个，和后端 tools_knowe.py 的 KNOWN_ROLES 一一对应
 *   （key = id 前缀 = 后端 KNOWN_ROLES 的 key；role = 后端 KNOWN_ROLES 的 value）。
 *   两边对不上，前端就认不出后端建的人，displayInfo 兜底成「未知」。改一头必须改另一头。
 */
export const DEFAULT_AGENTS: AgentRegistry = {
  coordinator: { name: i18n.t('common.06'), role: i18n.t('common.06'), roleEn: 'Leader', glyph: '总', pal: 'av-n', kind: 'agent' },
  zinnia: { name: i18n.t('common.19'), role: i18n.t('common.16'), roleEn: 'Receptionist', glyph: '知', pal: 'av-a', kind: 'platform', avatarUrl: './avatars/zinnia.png' },
  fe:     { name: i18n.t('state.11'), role: i18n.t('common.05'),        roleEn: 'Frontend',     glyph: '前', pal: 'av-b', kind: 'agent' },
  be:     { name: i18n.t('state.13'), role: i18n.t('contacts.view.05'),        roleEn: 'Backend',      glyph: '后', pal: 'av-c', kind: 'agent' },
  pm:     { name: i18n.t('state.04'), role: i18n.t('contacts.view.03'),        roleEn: 'PM',           glyph: '产', pal: 'av-d', kind: 'agent' },
  qa:     { name: i18n.t('state.26'), role: i18n.t('contacts.view.21'),        roleEn: 'QA',           glyph: '测', pal: 'av-d', kind: 'agent' },
  ux:     { name: i18n.t('state.32'), role: i18n.t('contacts.view.02'),  roleEn: 'Design',       glyph: '设', pal: 'av-a', kind: 'agent' },
  da:     { name: i18n.t('contacts.view.17'), role: i18n.t('contacts.view.17'),    roleEn: 'Data',         glyph: '数', pal: 'av-b', kind: 'agent' },
  devops: { name: i18n.t('state.34'), role: i18n.t('contacts.view.31'),        roleEn: 'DevOps',       glyph: '运', pal: 'av-c', kind: 'agent' },
  sec:    { name: i18n.t('state.16'), role: i18n.t('contacts.view.08'),        roleEn: 'Security',     glyph: '安', pal: 'av-d', kind: 'agent' },
  ml:     { name: i18n.t('state.01'),  role: i18n.t('contacts.view.01'), roleEn: 'ML',           glyph: '智', pal: 'av-a', kind: 'agent' },
  mobile: { name: i18n.t('contacts.view.23'),   role: i18n.t('contacts.view.23'),      roleEn: 'Mobile',       glyph: '移', pal: 'av-b', kind: 'agent' },
  game:   { name: i18n.t('state.27'), role: i18n.t('contacts.view.22'),        roleEn: 'Game',         glyph: '游', pal: 'av-c', kind: 'agent' },
  gis:    { name: i18n.t('state.02'), role: i18n.t('contacts.view.06'),    roleEn: 'GIS',          glyph: '图', pal: 'av-d', kind: 'agent' },
  mkt:    { name: i18n.t('state.31'), role: i18n.t('contacts.view.30'),        roleEn: 'Marketing',    glyph: '销', pal: 'av-a', kind: 'agent' },
  fin:    { name: i18n.t('state.33'), role: i18n.t('contacts.view.32'),   roleEn: 'Finance',      glyph: '财', pal: 'av-b', kind: 'agent' },
  hc:     { name: i18n.t('state.12'), role: i18n.t('contacts.view.04'),        roleEn: 'Healthcare',   glyph: '医', pal: 'av-c', kind: 'agent' },
  edu:    { name: i18n.t('state.15'), role: i18n.t('contacts.view.07'),   roleEn: 'Academic',     glyph: '学', pal: 'av-d', kind: 'agent' },
  ar:     { name: i18n.t('contacts.view.25'), role: i18n.t('contacts.view.25'),    roleEn: 'Spatial',      glyph: '空', pal: 'av-a', kind: 'agent' },
  sup:    { name: i18n.t('contacts.view.14'), role: i18n.t('contacts.view.14'),    roleEn: 'Support',      glyph: '支', pal: 'av-b', kind: 'agent' },
  sre:    { name: i18n.t('state.03'), role: i18n.t('contacts.view.26'),  roleEn: 'SRE',          glyph: '稳', pal: 'av-c', kind: 'agent' },
  db:     { name: i18n.t('contacts.view.18'),   role: i18n.t('contacts.view.18'),      roleEn: 'Database',     glyph: '库', pal: 'av-d', kind: 'agent' },
  arch:   { name: i18n.t('state.24'), role: i18n.t('contacts.view.19'),        roleEn: 'Architecture', glyph: '构', pal: 'av-a', kind: 'agent' },
  writer: { name: i18n.t('contacts.view.13'), role: i18n.t('contacts.view.13'),    roleEn: 'Writer',       glyph: '写', pal: 'av-b', kind: 'agent' },
  media:  { name: i18n.t('contacts.view.33'),   role: i18n.t('contacts.view.33'),      roleEn: 'Media',        glyph: '媒', pal: 'av-c', kind: 'agent' },
  legal:  { name: i18n.t('state.25'), role: i18n.t('contacts.view.20'),   roleEn: 'Legal',        glyph: '法', pal: 'av-d', kind: 'agent' },
};

/**
 * 默认角色类型（前缀 → 角色）
 *
 * [v0.10a Issue 4] ★ 与上面的 DEFAULT_AGENTS 和后端 KNOWN_ROLES 三方保持一致。
 *   type = id 前缀；label = 展示角色名；tpl = 去 DEFAULT_AGENTS 里取头像/glyph 的模板 key。
 */
export const DEFAULT_ROLE_TYPES: RoleType[] = [
  { type: 'fe',     label: i18n.t('common.05'),        tpl: 'fe' },
  { type: 'be',     label: i18n.t('contacts.view.05'),        tpl: 'be' },
  { type: 'pm',     label: i18n.t('contacts.view.03'),        tpl: 'pm' },
  { type: 'qa',     label: i18n.t('contacts.view.21'),        tpl: 'qa' },
  { type: 'ux',     label: i18n.t('contacts.view.02'),  tpl: 'ux' },
  { type: 'da',     label: i18n.t('contacts.view.17'),    tpl: 'da' },
  { type: 'devops', label: i18n.t('contacts.view.31'),        tpl: 'devops' },
  { type: 'sec',    label: i18n.t('contacts.view.08'),        tpl: 'sec' },
  { type: 'ml',     label: i18n.t('contacts.view.01'), tpl: 'ml' },
  { type: 'mobile', label: i18n.t('contacts.view.23'),      tpl: 'mobile' },
  { type: 'game',   label: i18n.t('contacts.view.22'),        tpl: 'game' },
  { type: 'gis',    label: i18n.t('contacts.view.06'),    tpl: 'gis' },
  { type: 'mkt',    label: i18n.t('contacts.view.30'),        tpl: 'mkt' },
  { type: 'fin',    label: i18n.t('contacts.view.32'),   tpl: 'fin' },
  { type: 'hc',     label: i18n.t('contacts.view.04'),        tpl: 'hc' },
  { type: 'edu',    label: i18n.t('contacts.view.07'),   tpl: 'edu' },
  { type: 'ar',     label: i18n.t('contacts.view.25'),    tpl: 'ar' },
  { type: 'sup',    label: i18n.t('contacts.view.14'),    tpl: 'sup' },
  { type: 'sre',    label: i18n.t('contacts.view.26'),  tpl: 'sre' },
  { type: 'db',     label: i18n.t('contacts.view.18'),      tpl: 'db' },
  { type: 'arch',   label: i18n.t('contacts.view.19'),        tpl: 'arch' },
  { type: 'writer', label: i18n.t('contacts.view.13'),    tpl: 'writer' },
  { type: 'media',  label: i18n.t('contacts.view.33'),      tpl: 'media' },
  { type: 'legal',  label: i18n.t('contacts.view.20'),   tpl: 'legal' },
];

// ═══════════════════════════════════════════════════════════════
// 三、身份解析（不变）
// ═══════════════════════════════════════════════════════════════

export function displayInfo(
  c: Conv,
  agentId: string,
  agents: AgentRegistry | null,
  roleTypes: RoleType[] | null,
): AgentDisplay {
  const m = c.members.find((x) => x.id === agentId);
  if (m?.display) return m.display;

  if (agents?.[agentId]) return agents[agentId] as AgentDisplay;

  const prefix = agentId.split('_')[0] ?? '';
  const rt = roleTypes?.find((r) => r.type === prefix);
  if (rt && agents?.[rt.tpl]) {
    const tpl = agents[rt.tpl] as AgentDisplay;
    return {
      name: rt.label + ' ' + (agentId.split('_').pop() ?? ''),
      role: tpl.role,
      roleEn: tpl.roleEn,
      glyph: tpl.glyph,
      pal: tpl.pal,
      kind: 'agent',
    };
  }

  if (agentId === 'coordinator' && agents?.coordinator) {
    return agents.coordinator;
  }

  /*
   * [v0.10a Issue 4] ★ 兜底不再静默说「Agent」。
   *
   *   走到这里意味着：这个 id 的前缀不在 DEFAULT_ROLE_TYPES 里 —— 要么后端建人时
   *   用了库外前缀（现在后端已经卡角色了，不该发生），要么是花名册之前的老数据。
   *   老代码返回 role:'Agent'，于是花名册里一排「Agent」，谁也不知道他们干什么，
   *   而且看起来像正常状态、不像出了错。改成「未知」并打一条 error：
   *   它是**异常信号**，不是一个真角色。（name 仍退回 id —— 这是最后一档，
   *   正常成员的名字/角色由 registerMember 从后端事件里填，到不了这里。）
   */
  console.error('[displayInfo] 未识别的 agent 前缀:', agentId.split('_')[0] ?? '', agentId);
  return {
    name: agentId,
    role: i18n.t('state.22'),
    roleEn: 'Unknown',
    glyph: agentId[0] || '?',
    pal: 'av-d',
    kind: 'agent',
  };
}

// ═══════════════════════════════════════════════════════════════
// 四、成员注册
// ═══════════════════════════════════════════════════════════════

/**
 * Register an agent in conv.members. Idempotent.
 *
 * ★ immer 冻结防护：static templates (DEFAULT_AGENTS) 永不进 draft。
 *   display 一律展开为新对象。
 */
export function registerMember(
  c: Conv,
  agentId: string,
  agents: AgentRegistry | null,
  roleTypes: RoleType[] | null,
  role?: string,
  name?: string,
  /**
   * [v0.9d Issue 2] 这条记录是不是**来自花名册**（agents_created / project_created.members）？
   *
   * true  = 后端此刻认为他在队里 → 如果他之前被归档了，**把他复活**。
   * false = 只是"这个 id 说过话"（stream_delta / message）→ 登记一下就行，
   *         **不碰 status**：一个已归档的人在历史消息里说过话，
   *         不代表他现在回来了。
   */
  active = false,
): void {
  // [v1.0.23.2] 角色中文化：后端花名册可能下发英文 role（Frontend/Backend/Coordinator…），
  //   按 id 前缀映射回中文（与 DEFAULT_ROLE_TYPES 一致）——主界面/转发弹窗/花名册统一显示中文。
  // [v1.0.23.3] 语言分流：中文模式保留强制中文化；英文模式不覆盖后端实时翻译值
  //   （后端 member_info/_display_role 已按当前语言下发），显示统一交给 roleLabel() 翻译。
  if (i18n.language !== 'en') {
    if (agentId === 'coordinator') {
      role = '项目经理';
    } else if (role) {
      const prefix = agentId.split('_')[0] ?? '';
      const rt = roleTypes?.find((r) => r.type === prefix);
      if (rt && rt.label !== role) role = rt.label;
    }
  }
  c.members = c.members || [];
  const existing = c.members.find((m) => m.id === agentId);

  if (existing) {
    /*
     * [v0.9c] ★ 人已经在册了，但**后来才知道他叫什么**。
     *
     *   顺序是这样的：一条 stream_delta 先到（registerMember 只拿到 agentId），
     *   agents_created 后到（这条才带 role 和 name）。
     *   老代码在这儿直接 return —— 于是**后端算好的名字永远进不来**。
     *   「幂等」不等于「后面的信息全都不要」。
     */
    if (role && existing.display.role !== role) existing.display.role = role;
    if (name && existing.display.name !== name) {
      existing.display.name = name;
      existing.display.glyph = name[0] || existing.display.glyph;
    }
    /*
     * ★★ [v0.9d Issue 2] **复活。** ★★
     *
     *   这条记录是从**花名册**来的（agents_created / project_created.members）——
     *   也就是说：后端此刻认为他**在队里**。那他就不该还挂着「已归档」。
     *
     *   老代码在这儿只更新了名字和角色就 return 了，status 一直留着 'removed'：
     *   于是归档的 fe_1 被加回来之后，名字对了、角色对了、**人还是灰的**，
     *   花名册面板把他归在「已归档」那一组，左栏宫格里也没有他——
     *   他复活了一半。
     *
     *   （active=false 的调用——stream_delta / message 那些——不碰 status：
     *     一个已归档的人在历史消息里说过话，不代表他现在回来了。）
     */
    if (active && existing.status === 'removed') existing.status = 'active';
    return;
  }

  {
    const base = displayInfo(c, agentId, agents, roleTypes);
    // ★ 展开复制——防止 immer 试图冻结静态模板上的属性
    const display: AgentDisplay = { ...base };

    /*
     * [v0.5b #4] ★ 这里是「项目经理头像换不掉」的**真根因**。
     *
     *   原来存的是 `pickAvatar(agentId)` —— 它只认 agentId，不认项目。
     *   而所有项目的项目经理 agentId 都叫 'coordinator'，于是确定性哈希给出同一张脸；
     *   `coordinatorAvatar()` 虽然写好了，却**从来没人调它**。
     *
     *   现在走 faceFor(agentId, 项目, 项目名) —— 它知道这是哪个项目的项目经理。
     *   花名册、.stack、气泡、审批卡全都读 display.avatarUrl，所以从源头修一次，
     *   四个地方一起对。
     */
    display.avatarUrl = base.avatarUrl
      || faceFor(agentId, c.projectId, c.projectName).avatarUrl;

    /*
     * ★★ [v0.9c] **名字不再由前端掷骰子。** ★★
     *
     *   老代码在这儿调 genPair() 随机生成一个中文/英文名，把 displayInfo() 刚算好的
     *   确定性名字（「前端 1」）**覆盖掉**。而 JS 上下文每次开 App 都是新的：
     *   重启 → members 清空 → 重新 registerMember → 重新掷骰子 →
     *   昨天叫「林知远」，今天叫「陈思涵」。
     *   **认人**是名字唯一的用处，而这个名字认不了人。
     *
     *   现在：名字是后端给的（agents_created / project_created 里的 name 字段，
     *   由花名册作证），后端没给才退回 displayInfo 的确定性名字（「前端 1」）。
     *   两条路都是确定的——重启一百次也是同一个名字。
     *
     *   （nameGenerator.ts 留着不删：它是个好玩的东西，将来做「给成员起个花名」
     *     这种功能还用得上。只是它不能再偷偷决定一个人叫什么。）
     */
    if (role) display.role = role;
    if (name) display.name = name;
    display.glyph = display.name?.[0] || display.glyph;

    c.members.push({
      id: agentId,
      status: 'active',                 // [v0.9d] 新人一律在队（归档只由 agent_removed 打标）
      /*
       * [v0.9c] state 是「他在不在忙」（idle | busy），**不是角色**。
       *   老代码写的是 `state: (role as 'idle' | 'busy') || 'idle'` ——
       *   把第五个参数（role，比如「前端」）硬塞进了 state。
       *   于是花名册面板右边那一栏显示的是「前端」，而不是「空闲」。
       *   一个 as 就把两个概念焊在了一起。新人一律 idle。
       */
      state: 'idle',
      display,
    });
  }
}

function eventMillis(ev: InboundEvent): number {
  const raw = (ev as unknown as { ts?: unknown }).ts;
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'string') {
    const parsed = Date.parse(raw);
    if (Number.isFinite(parsed)) return parsed;
  }
  return Date.now();
}

/** [v0.38.3 #3] 事件 seq（用于「跳转到消息出处」把气泡和记录对上）。取不到 → undefined。 */
function eventSeq(ev: InboundEvent): number | undefined {
  const raw = (ev as unknown as { seq?: unknown }).seq;
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : undefined;
}

type ActivityLike = {
  scope_id?: unknown;
  task_id?: unknown;
  attempt_id?: unknown;
  run_id?: unknown;
  completion_id?: unknown;
  channel_id?: unknown;
};

type ActivityIdentity = { scopeId: string; channelId: string };

const LEGACY_ACTIVITY_SCOPE = 'legacy-single-turn';

function activityIdentity(c: Conv, ev: ActivityLike): ActivityIdentity {
  const taskId = optionalString(ev.task_id);
  const attemptId = optionalString(ev.attempt_id);
  const scopeId = optionalString(ev.scope_id)
    || (taskId && attemptId ? `task:${taskId}:attempt:${attemptId}` : undefined)
    || optionalString(ev.run_id)
    || optionalString(ev.completion_id)
    || LEGACY_ACTIVITY_SCOPE;
  return {
    scopeId,
    channelId: optionalString(ev.channel_id) || c.projectId,
  };
}

function activityScopeKey(identity: ActivityIdentity): string {
  return `${identity.channelId}\u0000${identity.scopeId}`;
}

function itemMatchesActivity(
  item: AgentItem,
  agentId: string,
  identity: ActivityIdentity,
): boolean {
  return item.agentId === agentId
    && (item.scopeId || LEGACY_ACTIVITY_SCOPE) === identity.scopeId
    && (item.channelId || identity.channelId) === identity.channelId;
}

function markMemberBusy(c: Conv, agentId: string, ev: InboundEvent): void {
  const member = c.members.find((m) => m.id === agentId);
  if (!member || member.status === 'removed') return;
  const identity = activityIdentity(c, ev);
  const key = activityScopeKey(identity);
  const activeScopes = member.activeScopes || (member.activeScopes = {});
  if (activeScopes[key] !== undefined) {
    member.state = 'busy';
    return;
  }
  const latest = c.members.reduce((max, m) => Math.max(max, m.busySince ?? 0), 0);
  const startedAt = Math.max(eventMillis(ev), latest + 1);
  activeScopes[key] = startedAt;
  member.state = 'busy';
  // A second concurrent scope must not make an already-busy member jump in ordering.
  member.busySince = member.busySince ?? startedAt;
}

function markMemberIdle(c: Conv, agentId: string, ev: InboundEvent): void {
  const member = c.members.find((m) => m.id === agentId);
  if (!member) return;
  const activeScopes = member.activeScopes || {};
  delete activeScopes[activityScopeKey(activityIdentity(c, ev))];
  member.activeScopes = activeScopes;
  const remaining = Object.values(activeScopes);
  if (remaining.length) {
    member.state = 'busy';
    member.busySince = Math.min(...remaining);
    return;
  }
  member.state = 'idle';
  member.busySince = undefined;
}

/**
 * [v1.0.24.4] 用后端权威活动账本校准整个花名册的忙碌状态。
 *
 * 触发时机：state_snapshot / replay_complete 携带 activity 字段（同步边界）。
 * 语义（与后端 server.py「引擎不在 → 空列表 → 校准回 idle」同义）：
 *   - 在账本里的成员 → 忙：activeScopes **整体替换**为账本的 scope 键集，
 *     busySince 取账本中最早的 started_at（与 live 事件累加的 busySince 语义一致）；
 *   - 不在账本里的成员 → 空闲：activeScopes 清空、busySince 清除。
 *
 * 键同构（自愈闭环的命门）：账本条目的 scope_id/channel_id 是后端
 * _correlate_visible_event 补全后的权威身份，拼出来的键与 live agent_active
 * 事件写入、agent_idle 事件删除的键**完全相同**——校准不会造出关不掉的忙碌态。
 *
 * 频道过滤：账本是整个群引擎的（含私聊频道条目），只取 channel_id === c.projectId
 * 的条目校准本会话——私聊忙碌态归私聊会话自己校准，不串到群花名册。
 */
export function calibrateRosterActivity(c: Conv, activity: ActivityLedgerEntry[]): void {
  const busyByMember = new Map<string, Record<string, number>>();
  for (const entry of activity) {
    if (entry.channel_id !== c.projectId) continue;
    const key = activityScopeKey({ scopeId: entry.scope_id, channelId: entry.channel_id });
    const scopes = busyByMember.get(entry.agent_id) ?? {};
    scopes[key] = scopes[key] === undefined
      ? entry.started_at
      : Math.min(scopes[key], entry.started_at);
    busyByMember.set(entry.agent_id, scopes);
  }
  for (const member of c.members) {
    if (member.status === 'removed') continue;
    const scopes = busyByMember.get(member.id);
    if (scopes) {
      member.activeScopes = scopes;
      member.state = 'busy';
      member.busySince = Math.min(...Object.values(scopes));
    } else {
      member.activeScopes = {};
      member.state = 'idle';
      member.busySince = undefined;
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// [v0.40.2 #1] LLM 供应商错误 → 人性化中文提示
// ═══════════════════════════════════════════════════════════════

/**
 * 「余额不足」时给用户看的中文提示（README §一）。所有调用 LLM 的地方共用同一句
 * ——翻译、聊天回复……——不再把 "Insufficient" 之类的英文或 HTTP 402 状态码直接抛给用户。
 */
export const INSUFFICIENT_BALANCE_MSG = i18n.t('state.14');

/**
 * 这条错误是不是「供应商余额不足」。
 * 判据（README §一）：错误文本里含 "Insufficient" 关键词，或 HTTP 402 状态码。
 */
export function isInsufficientBalance(text: string | undefined | null, status?: number): boolean {
  if (status === 402) return true;
  const s = (text ?? '').toString();
  if (!s) return false;
  return /insufficient/i.test(s) || /\b402\b/.test(s);
}

/**
 * 把一条可能来自 LLM 供应商的错误文本换成人性化中文：命中「余额不足」→ 中文提示；
 * 否则原样返回（其它错误照旧显示，不误伤）。用于聊天回复的 error 事件、翻译失败等
 * 一切面向用户的 LLM 报错。
 */
export function humanizeLlmError(raw: string | undefined | null): string {
  const s = (raw ?? '').toString();
  return isInsufficientBalance(s) ? INSUFFICIENT_BALANCE_MSG : s;
}

// ═══════════════════════════════════════════════════════════════
// 五、applyEvent(c, ev) — 纯状态突变（v2 重写）
// ═══════════════════════════════════════════════════════════════

interface ApplyEventOptions {
  /** Snapshot/history replay already crossed a render boundary; only live events need frame protection. */
  protectTransientFrame?: boolean;
}

export function applyEvent(
  c: Conv,
  ev: InboundEvent,
  agents: AgentRegistry | null,
  roleTypes: RoleType[] | null,
  options: ApplyEventOptions = {},
): void {
  c.items = c.items || [];
  const protectTransientFrame = options.protectTransientFrame !== false;

  /*
   * [v0.10b Bug1C/6B] stream_reset：引擎判定项目经理刚才那段是"说谎"（说团队不可改，
   *   或没调工具却说「已经加回来了」），要重讲。把当前**还在流**的那个气泡清空
   *   （text=''），紧接着重跑的 stream_delta 会从这个干净气泡续写——否则就变成
   *   「坏话 + 纠正后的话」拼在一起。
   *
   *   它不在 InboundEvent 联合类型里（契约文件不在本次改动范围），所以在 switch
   *   之外用字符串判定接住它，避开 TS 的联合类型检查。未知事件本来也会走到下面
   *   switch 的 default（console.warn + 忽略），这里只是提前把它接住并真正处理。
   */
  if ((ev as { type?: string }).type === 'completion_status') {
    applyCompletionStatus(
      c, ev as unknown as CompletionStatusLike, agents, roleTypes, protectTransientFrame,
    );
    return;
  }

  if ((ev as { type?: string }).type === 'completion_view_v1') {
    if (featureEnabled('COMPLETION_VIEW_V1')) {
      applyCompletionViewV1(
        c, ev as unknown as CompletionViewV1Like, agents, roleTypes,
      );
    }
    return;
  }

  /*
   * [v0.44.12] 后端在组队提案未通过时会额外发 agents_rejected。
   * 这个事件不在旧版 InboundEvent 联合类型里，所以和 stream_reset 一样在 switch 前接住。
   *
   * ★ 删除条件是双重精确匹配：
   *   ① id 必须出现在本次 rejected.members；
   *   ② id 必须被某张审批卡记为 provisionalMemberIds（即卡到达时原本不在花名册）。
   * 这样既不会误删 pm_10 之类相似 id，也不会删掉恢复提案里的 pm_1 归档身份。
   */
  if ((ev as { type?: string }).type === 'agents_rejected') {
    const rejectedMembers = (ev as { members?: { id?: unknown }[] }).members;
    const rejectedIds = new Set(
      Array.isArray(rejectedMembers)
        ? rejectedMembers
          .map((m) => (typeof m?.id === 'string' ? m.id : ''))
          .filter((id): id is string => id.length > 0)
        : [],
    );
    if (rejectedIds.size) {
      const removable = new Set<string>();
      for (const item of c.items) {
        if (item.kind !== 'approval' || item.state === 'confirmed') continue;
        for (const id of item.provisionalMemberIds || []) {
          if (rejectedIds.has(id)) removable.add(id);
        }
        item.provisionalMemberIds = (item.provisionalMemberIds || [])
          .filter((id) => !rejectedIds.has(id));
      }
      if (removable.size) {
        c.members = (c.members || []).filter((m) => !removable.has(m.id));
      }
    }
    return;
  }

  switch (ev.type) {

    // ── lifecycle：唯一 roster 可用性真相，按 channel + scope 对称闭合 ──
    case 'agent_active': {
      registerMember(c, ev.agent_id, agents, roleTypes);
      const identity = activityIdentity(c, ev);
      markMemberBusy(c, ev.agent_id, ev);
      ensureActivityBubble(c, ev.agent_id, identity, ev, protectTransientFrame);
      break;
    }

    case 'agent_idle': {
      registerMember(c, ev.agent_id, agents, roleTypes);
      const identity = activityIdentity(c, ev);
      markMemberIdle(c, ev.agent_id, ev);
      finishAgentStages(c, ev.agent_id, identity, 'complete');
      settleAgentStreaming(c, ev.agent_id, identity);
      break;
    }

    case 'stream_reset': {
      const identity = activityIdentity(c, ev);
      const idx = findLastStreamingIndex(c.items, ev.agent_id, identity);
      if (idx >= 0) (c.items[idx] as AgentItem).text = '';
      break;
    }

    // ── user_echo：乐观渲染确认 ───────────────────────────
    case 'user_echo': {
      // [v1.0.23.3 D-5] 用户手动发新消息 → 之前未点击的四方向按钮全部消失（不堆积）
      for (const it of c.items) {
        if (it.kind === 'agent' && it.suggestions) delete it.suggestions;
      }
      // 匹配 cmid → pending→confirmed
      const cmid = ev.client_msg_id ?? undefined;
      if (cmid) {
        const pendingItem = c.items.find(
          (it): it is UserItem => it.kind === 'user' && it.cmid === cmid,
        );
        if (pendingItem && pendingItem.delivery === 'pending') {
          pendingItem.delivery = 'confirmed';
          if (ev.attachments && ev.attachments.length) pendingItem.attachments = ev.attachments;
          break;
        }
      }
      // 未找到匹配（旧客户端发的不乐观消息或已确认）
      if (cmid) {
        const existing = c.items.find(
          (it): it is UserItem => it.kind === 'user' && it.cmid === cmid,
        );
        if (existing) break; // 已存在（confirmed/suspect）→ 跳过
      }
      c.items.push({
        kind: 'user',
        // [v1.0.23.1] 转发消息重放：主文案 = 配言（forwarded.comment），模板串永不上屏（修复 B4）。
        text: ev.forwarded?.comment !== undefined ? ev.forwarded.comment : ev.content,
        cmid: cmid || '',
        delivery: 'confirmed',
        ts: eventMillis(ev),   // [v0.38] 时间分隔线用
        seq: eventSeq(ev),     // [v0.38.3 #3] 跳转定位用
        // [v1.0.23.1] 转发结构随回声落盘/重放，恢复引用窗。
        ...(ev.forwarded ? { forwarded: ev.forwarded } : {}),
        ...(ev.attachments && ev.attachments.length ? { attachments: ev.attachments } : {}),
      });
      break;
    }

    // ── stream_delta：流式增量 ────────────────────────────
    case 'stream_delta': {
      registerMember(c, ev.agent_id, agents, roleTypes);
      const identity = activityIdentity(c, ev);
      const sIdx = findLastStreamingIndex(c.items, ev.agent_id, identity);
      const sItem = sIdx >= 0 ? (c.items[sIdx] as AgentItem) : undefined;

      /*
       * [v0.7b #4] ★ 卡片一插进来，它上面那个气泡就**再也不许长了**。
       *
       *   项目经理说到一半去调 propose_agents → 审批卡插在气泡下面 → 用户点确认 →
       *   项目经理接着说。原来的写法用 findLast 找「这个 agent 最后一个还在流的气泡」，
       *   找到的是**卡片上面那个旧气泡**，于是新说的话被追加进去——
       *   屏幕上看起来是卡片把消息「顶」上去了，时间顺序整个乱掉：
       *   卡片下面明明什么都没有，可它上面的气泡却在长。
       *
       *   所以：气泡后面只要插进过别人的东西（审批卡、系统行），这条流就算被打断了。
       *   旧气泡就地定格，新的话从**卡片下面**重新起一个气泡。
       */
      if (sItem && !blockedAfter(c.items, sIdx)) {
        armTransientFrame(sItem, protectTransientFrame);
        sItem.text += ev.content;
      } else {
        if (sItem) settleStreamingItem(sItem);   // 定格：旧气泡从此不再变
        c.items.push({
          kind: 'agent',
          agentId: ev.agent_id,
          scopeId: identity.scopeId,
          channelId: identity.channelId,
          text: ev.content,
          streaming: true,   // [v0.3-UI 编译修复] 删除重复键（TS1117）
          ...(protectTransientFrame ? { transientFrame: newTransientFrame() } : {}),
          ts: eventMillis(ev),   // [v0.38] 流起始时刻，定格后沿用
          seq: eventSeq(ev),     // [v0.38.3 #3] 起始 seq，message 定格时会覆盖成 final seq
        });
      }
      break;
    }

    // ── [v1.0.23.3] reasoning_delta：推理增量（流式实时累积，message 落定后权威覆盖） ──
    case 'reasoning_delta': {
      registerMember(c, ev.agent_id, agents, roleTypes);
      const identity = activityIdentity(c, ev);
      let sIdx = findLastStreamingIndex(c.items, ev.agent_id, identity);
      if (sIdx < 0) {
        // [v1.0.23.5] worker 无正文流：推理增量先于正文流到达且 worker 不产生
        //   stream_delta/message(streaming)（WorkerRuntime 只转发推理），旧逻辑
        //   「无 streaming item → 忽略」把 worker 的推理全丢了。
        //   现在与 stream_delta 同构：无流式气泡时先建「推理占位」气泡
        //   （三点 + 推理面板），落定 message(completion_id) 到达时被替换成完整消息。
        const placeholder: AgentItem = {
          kind: 'agent',
          agentId: ev.agent_id,
          scopeId: identity.scopeId,
          channelId: identity.channelId,
          text: '',
          streaming: true,   // 占位即流式态：统一壳渲染三点 + 推理面板
          ...(protectTransientFrame ? { transientFrame: newTransientFrame() } : {}),
          ts: eventMillis(ev),   // [v0.38] 流起始时刻，定格后沿用
          seq: eventSeq(ev),     // [v0.38.3 #3] 起始 seq，message 定格时会覆盖成 final seq
        };
        c.items.push(placeholder);
        sIdx = c.items.length - 1;
      }
      const sItem = sIdx >= 0 ? (c.items[sIdx] as AgentItem) : undefined;
      if (sItem && !blockedAfter(c.items, sIdx)) {
        armTransientFrame(sItem, protectTransientFrame);
        const chunk = (ev as { content?: unknown }).content;
        if (typeof chunk === 'string' && chunk) {
          sItem.reasoning = (sItem.reasoning ?? '') + chunk;
        }
      }
      break;
    }

    // ── [v1.0.23.3] suggestions：四方向建议（辅助 LLM 异步生成，瞬时不落盘） ──
    case 'suggestions': {
      registerMember(c, ev.agent_id, agents, roleTypes);
      const rawItems = (ev as { items?: unknown }).items;
      if (!Array.isArray(rawItems) || rawItems.length === 0) break;
      const clean: SuggestionItem[] = [];
      for (const raw of rawItems) {
        if (raw && typeof raw === 'object') {
          const title = optionalString((raw as { title?: unknown }).title);
          if (title) {
            clean.push({
              title,
              sub: optionalString((raw as { sub?: unknown }).sub) ?? '',
            });
          }
        }
      }
      if (clean.length === 0) break;
      // 挂到该 agent 最近一条已定格的 agent 气泡上（suggestions 在 message 后异步到达）
      for (let i = c.items.length - 1; i >= 0; i--) {
        const it = c.items[i];
        if (it && it.kind === 'agent' && it.agentId === ev.agent_id && !it.streaming) {
          it.suggestions = clean;
          break;
        }
      }
      break;
    }

    // ── message：流式收尾 / 完整消息 ──────────────────────
    case 'message': {
      registerMember(c, ev.agent_id, agents, roleTypes);
      const identity = activityIdentity(c, ev);

      // [v0.36] 本轮产出的文件（后端捎在 message 上，纯附载）。它跟着**定格后的**
      //   那个气泡走：谁承接了这条 final 文本，files 就挂到谁身上。
      const files = producedFilesOf(ev);
      const completionId = optionalString((ev as { completion_id?: unknown }).completion_id);
      if (completionId) {
        let existingIdx = findCompletionItemIndex(c.items, completionId);
        if (existingIdx < 0) {
          // [v1.0.23.5] worker 推理占位气泡落定：reasoning_delta 建的占位 item 没有
          //   completionId（推理事件不带），completion 消息按 id 找不到 → 回退替换该
          //   agent 最近的无 completionId streaming 占位（scope 优先，宽松兜底防残留）。
          const fbIdentity = activityIdentity(c, ev);
          for (let i = c.items.length - 1; i >= 0; i--) {
            const it = c.items[i];
            if (it && it.kind === 'agent'
                && it.agentId === ev.agent_id
                && it.streaming
                && !it.completionId
                && (!it.scopeId || !fbIdentity.scopeId || it.scopeId === fbIdentity.scopeId)) {
              existingIdx = i;
              break;
            }
          }
        }
        if (existingIdx >= 0) {
          const existing = c.items[existingIdx]!;
          const version = completionVersionOf(ev);
          if (!shouldReplaceCompletionProjection(
            completionItemClock(existing),
            { version, authority: 'message' },
          )) {
            // [v1.0.23.5] 兜底：message 被权威投影（completion_view_v1，authority 更高）
            //   拒收时，若事件带推理而现有气泡没有（旧数据/旧时序的 view_v1 无推理），
            //   补写推理——不碰 text/authority，仅补齐缺口。
            if (existing.kind === 'agent' && !(existing as AgentItem).reasoning) {
              applyReasoningFields(existing as AgentItem, ev as unknown as Record<string, unknown>);
            }
            // [v1.0.25.3] 跳转对齐：抽屉 /history 只留 message 事件（worker 完成时
            //   completion_view_v1 与 message 各占一个 seq），即使本事件被拒收，
            //   气泡 seq 也要收尾成 message 的 seq——否则「跳转到消息出处」按抽屉
            //   seq 找不到气泡（气泡挂在 view_v1 的 seq 上），点击无反应。
            if (existing.kind === 'agent') {
              const finalSeq = eventSeq(ev);
              if (typeof finalSeq === 'number') (existing as AgentItem).seq = finalSeq;
            }
            break;
          }

          const authoritative: AgentItem = existing.kind === 'agent'
            ? existing
            : {
              kind: 'agent',
              agentId: ev.agent_id,
              text: '',
              streaming: false,
            };
          const wasStreaming = authoritative.streaming === true;
          authoritative.agentId = ev.agent_id;
          authoritative.scopeId = identity.scopeId;
          authoritative.channelId = identity.channelId;
          authoritative.text = ev.content || authoritative.text;
          authoritative.ts = eventMillis(ev);
          authoritative.seq = eventSeq(ev);
          authoritative.completionId = completionId;
          authoritative.completionStatus = optionalString((ev as { status?: unknown }).status)
            || authoritative.completionStatus;
          authoritative.completionVersion = version;
          authoritative.completionAuthority = 'message';
          authoritative.completionTerminal = booleanOrUndefined(
            (ev as { terminal?: unknown }).terminal,
          ) ?? authoritative.completionTerminal;
          authoritative.completionTransient = false;
          authoritative.completionGaps = stringArray((ev as { gaps?: unknown }).gaps);
          authoritative.completionNextActions = stringArray(
            (ev as { next_actions?: unknown }).next_actions,
          );
          if (files.length) authoritative.files = files;
          applyReasoningFields(authoritative, ev);   // [v1.0.23.3]
          finishMessageStages(authoritative, ev);
          if (wasStreaming) settleStreamingItem(authoritative);
          else authoritative.streaming = false;
          c.items.splice(existingIdx, 1, authoritative);
          removeDuplicateCompletionItems(c.items, completionId, existingIdx);
          if (authoritative.completionTerminal !== false) {
            settleAgentStreaming(c, ev.agent_id, identity);
          }
          break;
        }
      }

      // 空 content 且无进行中流 → 不渲染空气泡（§2.1#3）。
      // 消息内容不是可用性信号；只有 agent_idle 能清除工作态。
      const hasStreaming = c.items.some(
        (it): it is AgentItem => it.kind === 'agent'
          && itemMatchesActivity(it, ev.agent_id, identity)
          && !!it.streaming,
      );
      if (!ev.content && !hasStreaming) {
        // [v0.36] 写了文件却没说话（罕见：只交报告 / 空返回）→ 仍挂一张卡。
        //   空文本 + 有 files 的气泡**不是空气泡**，渲染层放行（只画文件卡）。
        if (files.length) {
          c.items.push({
            kind: 'agent', agentId: ev.agent_id,
            scopeId: identity.scopeId, channelId: identity.channelId,
            text: '', streaming: false, files,
            ...(completionId ? completionMessageMeta(ev, completionId) : {}),
            ts: eventMillis(ev),   // [v0.38]
            seq: eventSeq(ev),     // [v0.38.3 #3]
          });
        }
        break;
      }

      // 收尾进行中的流
      const mIdx = findLastStreamingIndex(c.items, ev.agent_id, identity);
      const streamingItem = mIdx >= 0 ? (c.items[mIdx] as AgentItem) : undefined;

      /*
       * [v0.7b #4] 收尾也要认那条打断线。
       *
       *   要是那个「还在流的气泡」被审批卡压在上面（比如非流式档：一个 delta 都没来，
       *   直接来了 message），把最终文案写回**卡片上方的旧气泡**，还是同一个错位。
       *   这时候旧气泡定格，最终文案另起一个气泡落在卡片下面。
       */
      if (streamingItem && blockedAfter(c.items, mIdx)) {
        finishMessageStages(streamingItem, ev);
        settleStreamingItem(streamingItem);
        const text = dedupeFinal(c.items, ev.agent_id, identity, ev.content || '', c.items.length);
        // [v0.36] 有正文 → 新气泡承接正文与 files；无正文但有 files → 起一张纯文件卡气泡。
        if (text || files.length) {
          const newItem: AgentItem = {
            kind: 'agent',
            agentId: ev.agent_id,
            scopeId: identity.scopeId,
            channelId: identity.channelId,
            text,
            streaming: false,
            ts: eventMillis(ev),   // [v0.38]
            seq: eventSeq(ev),     // [v0.38.3 #3] final message 的 seq
            ...(files.length ? { files } : {}),
            ...(completionId ? completionMessageMeta(ev, completionId) : {}),
          };
          applyReasoningFields(newItem, ev);   // [v1.0.23.3]
          c.items.push(newItem);
        }
      } else if (streamingItem) {
        const text = dedupeFinal(c.items, ev.agent_id, identity, ev.content || '', mIdx);
        streamingItem.text = text || streamingItem.text;
        streamingItem.seq = eventSeq(ev);   // [v0.38.3 #3] 定格时用 final message 的 seq 覆盖
        // [v0.36] files 挂到这个刚定格的气泡上（它就是本轮那条可见消息）。
        if (files.length) streamingItem.files = files;
        if (completionId) {
          streamingItem.completionId = completionId;
          streamingItem.completionStatus = optionalString((ev as { status?: unknown }).status);
          streamingItem.completionTransient = false;
          streamingItem.completionVersion = completionVersionOf(ev);
          streamingItem.completionAuthority = 'message';
          streamingItem.completionTerminal = booleanOrUndefined(
            (ev as { terminal?: unknown }).terminal,
          );
        }
        finishMessageStages(streamingItem, ev);
        applyReasoningFields(streamingItem, ev);   // [v1.0.23.3]
        settleStreamingItem(streamingItem);
      } else {
        // [v0.10b Bug2] ★ dedup 之后可能变成空串（final 整段被卡片上方那截「念过一遍」了）。
        //   空气泡渲染时会被 ChatStream 过滤成 null，但它**仍然留在 items 里**，
        //   于是紧跟其后的同一发送者气泡会被判成「同组」→ 头像整行被隐藏
        //   （拒绝审批后项目经理跟进那句就是这么丢了头像的）。
        //   所以：dedup 后为空就干脆不 push，不留这个幽灵气泡。
        //   [v0.36] 例外：dedup 后为空但**有 files** → 仍要 push（纯文件卡气泡，不是幽灵）。
        const text = dedupeFinal(c.items, ev.agent_id, identity, ev.content || '', c.items.length);
        if (text || files.length) {
          const newItem: AgentItem = {
            kind: 'agent',
            agentId: ev.agent_id,
            scopeId: identity.scopeId,
            channelId: identity.channelId,
            text,
            streaming: false,  // [v0.3-UI 编译修复] 删除重复键（TS1117）
            ts: eventMillis(ev),   // [v0.38]
            seq: eventSeq(ev),     // [v0.38.3 #3]
            ...(files.length ? { files } : {}),
            ...(completionId ? completionMessageMeta(ev, completionId) : {}),
          };
          applyReasoningFields(newItem, ev);   // [v1.0.23.3]
          c.items.push(newItem);
        }
      }
      break;
    }

    // ── approval_card：审批卡 ─────────────────────────────
    case 'approval_card': {
      const norm = normalizeApprovalCard(ev);
      // [v0.9b] 三种卡：组队(team) · 派活(task) · 移除(remove)
      const tool = norm.tool === 'propose_agents' ? 'team' : (
        norm.tool === 'propose_next' ? 'task' : (
          norm.tool === 'propose_remove_agent' ? 'remove' : norm.tool
        )
      );

      const existingIdx = c.items.findIndex(
        (it) => it.kind === 'approval' && it.cardId === norm.card_id,
      );
      const existingApproval = existingIdx >= 0
        ? (c.items[existingIdx] as ApprovalItem)
        : undefined;
      const provisionalIds = new Set(existingApproval?.provisionalMemberIds || []);

      // 注册提议的成员
      if (norm.tool === 'propose_agents') {
        // [v0.10a Issue 1] ★ proposed 里带着后端算好的名字（v0.9c）——注册时就把它填进去。
        //   否则审批卡先显示占位名（前端认前缀算出的「前端 1」，库外前缀甚至是 id），
        //   点确认后 agents_created 再把名字改成「林知远」——卡上的人当场换了个名，
        //   用户会以为来的不是同一个（v0.8e 修过的老伤）。名字从一开始就填对，卡才稳。
        const card = ev.card as { proposed?: { id: string; role: string; name?: string }[] };
        const currentIds = new Set(
          (card.proposed || [])
            .map((a) => (typeof a.id === 'string' ? a.id : ''))
            .filter((id): id is string => id.length > 0),
        );

        // 同一张卡被「我有新意见」原地改写时，撤掉已经不在新卡里的旧临时成员。
        const dropped = new Set(
          [...provisionalIds].filter((id) => !currentIds.has(id)),
        );
        if (dropped.size) {
          c.members = (c.members || []).filter((m) => !dropped.has(m.id));
          for (const id of dropped) provisionalIds.delete(id);
        }

        card.proposed?.forEach((a) => {
          const alreadyKnown = (c.members || []).some((m) => m.id === a.id);
          registerMember(c, a.id, agents, roleTypes, a.role, a.name);
          if (!alreadyKnown) provisionalIds.add(a.id);
        });
      }

      /*
       * [v0.7b #4 → v0.30 Bug4] 卡片落地之前，把**提议它的那个人**还在流的气泡定格。
       *
       *   后端此刻正卡在闸门上等人点头（gate.propose 是 await 的）——但卡住的
       *   只有**提议者**（项目经理）。他的气泡不会再有新 delta 了，让光标转下去是撒谎。
       *
       *   ★ 只定格他一个人的。v0.28 这里写的是「定格所有人」——那时候回合串行，
       *     「所有人」和「提议者」是同一个人，怎么写都对。v0.29 之后 Worker 在
       *     后台并发干活：他的「正在工作」气泡（agent_thinking 挂的空 streaming
       *     气泡 + tool_gen 叠的活动行）和这张卡**毫无关系**。一并冻掉，空文本
       *     气泡就地蒸发——这正是 Bug 4「新卡片弹出 → 工作中成员的状态气泡消失」。
       *     Worker 状态气泡从此只服从他自己的生命周期事件（thinking / message /
       *     agent_idle），不再被别人的卡片误伤（重构原则 5）。
       *   （blockedAfter 那两道锁照旧兜底：万一提议者的旧气泡漏冻，卡后的新 delta
       *     也会另起新气泡，不会追写到卡上面去。）
       */
      const proposer = (ev as { agent_id?: string }).agent_id;
      if (proposer) {
        const proposerBubble = findLastStreamingIndex(c.items, proposer);
        if (proposerBubble >= 0) {
          const item = c.items[proposerBubble] as AgentItem;
          finishWorkStages(stagesOf(item), 'waiting', 'wait', i18n.t('state.28'), STAGE_MAX);
          settleStreamingItem(item);
          // [v1.0.24.4-r14] 派卡接力：推理定格后即将出卡 → 推理面板保持展开，
          //   不折叠成小条（收起动画从完整高度开始；避免「缩了一下」突变）。
          item.relayPending = true;
        }
      }

      /*
       * ★ [v0.26] 这个 card_id 已经在了 → **原地更新，不新建**。
       *
       *   这就是「我有新意见」的原地 morph 本身：用户提了意见 → 后端把卡上的
       *   instruction 换了一版 → 重发一条同 card_id 的 approval_card。
       *   item 还待在**原来那一格**，只有 card 里的字变了 →
       *   React 重渲染 → CSS 过渡把高度平滑地推开。
       *   **不是「旧卡落窄条 + 新卡弹出来」，是同一张卡自己变了。**
       *
       *   为什么这条语义天然就对（而不是为了 morph 硬凑的）：
       *     · **卡的身份就是 card_id**。同一个 id 再来一次，只可能是「它变了」。
       *     · 幂等：重放 ring / 重建快照时，后一条覆盖前一条 → 终态正确。
       *       要是新造一个 approval_card_updated 事件，回放这条路得另外再想一遍。
       *     · 位置不动 → v0.7b #4 的 blockedAfter 那三道锁一个都不用碰。
       *
       *   注意：**只更新 card 内容，不碰 state**。它可能已经是 confirmed/rejected 了
       *   （用户手快，在调整回来之前就点了确认）——那是「首个解决为准」的铁律，
       *   一张已经落定的卡不许被一条迟到的 approval_card 拉回 pending。
       *
       *   [v0.30 Bug2/3] rev +1：不管卡面变没变，这条事件本身就是后端对
       *   「我有新意见」的**回执**——失败时后端会故意重播一条一字不变的卡。
       *   ApprovalCard 组件靠 rev 收转圈（详见 ApprovalItem.rev 的注释）。
       */
      if (existingIdx >= 0) {
        const existing = c.items[existingIdx] as ApprovalItem;
        existing.card = ev.card;
        existing.expiresAt = (ev.card as Record<string, unknown>).expires_at as string
          || existing.expiresAt;
        if (proposer) existing.agentId = existing.agentId || proposer;
        existing.provisionalMemberIds = [...provisionalIds];
        existing.rev = (existing.rev ?? 1) + 1;
        break;
      }

      c.items.push({
        kind: 'approval',
        cardId: norm.card_id,
        projectId: (ev as Record<string, unknown>).project_id as string || '',
        tool,
        card: ev.card,
        state: 'pending',
        expiresAt: (ev.card as Record<string, unknown>).expires_at as string || '',
        recovered: (ev.card as Record<string, unknown>).recovered as boolean | undefined,
        agentId: proposer,
        provisionalMemberIds: [...provisionalIds],
        rev: 1,
      });
      break;
    }

    // ── approval_resolved：审批解决 ───────────────────────
    case 'approval_resolved': {
      const resolvedIdx = c.items.findIndex(
        (it) => it.kind === 'approval' && it.cardId === ev.card_id,
      );
      const resolvedItem = resolvedIdx >= 0
        ? (c.items[resolvedIdx] as ApprovalItem)
        : undefined;
      if (resolvedItem && resolvedItem.state === 'pending') {
        resolvedItem.state =
          ev.resolution === 'approved'  ? 'confirmed' :
          ev.resolution === 'rejected'  ? 'rejected'   :
          ev.resolution === 'timeout'   ? 'timeout'    : 'cancelled';
      }

      /*
       * [v0.7b #4 → v0.30 Bug4] 卡一落定，它**上面**、且属于**卡主**的流式气泡就地定格。
       *
       *   正常路径上 approval_card 已经定格过一遍了；这里是兜底——
       *   快照重建时事件是重放的（顺序可能不同）、崩溃恢复会复提卡片，
       *   总有漏网的。定格这件事是幂等的，多做一次不会错。
       *   卡片**下面**的流不动：那是确认之后新起的那个气泡，它正说着话呢。
       *
       *   ★ 只动卡主的（resolvedItem.agentId，落卡时记下的提议者）。
       *     别人（后台干活的 Worker）的气泡跟这张卡无关——一并冻掉就是 Bug 4。
       *     老卡没记 agentId（快照重放的旧数据）→ 退回冻所有人：宁可多冻一次
       *     （blockedAfter 会让后续 delta 另起气泡，视觉无损），不可漏掉卡主。
       */
      if (resolvedIdx >= 0) {
        const owner = resolvedItem?.agentId;
        for (let i = resolvedIdx - 1; i >= 0; i--) {
          const it = c.items[i];
          if (it && it.kind === 'agent' && it.streaming
              && (!owner || it.agentId === owner)) {
            finishWorkStages(stagesOf(it), 'complete', undefined, undefined, STAGE_MAX);
            settleStreamingItem(it);
            break;
          }
        }
      }
      // 已落终态 → 幂等忽略

      /*
       * [v0.37.3 → v0.44.12] 拒绝组队提案，只清理**这张卡临时新建**的成员。
       *
       * 旧逻辑拿 card.proposed 的 id 直接 filter 整份 c.members。虽然 Set.has 是精确相等，
       * 但它不知道这条 id 在卡到达前是否已经存在：恢复 pm_1 的提案被拒时，pm_1 是一条
       * 必须保留的归档身份，不是可以删除的乐观占位。provisionalMemberIds 在落卡时记录了
       * 「原本不存在」这一事实，因此这里只做 proposed ∩ provisional 的双重精确删除。
       */
      if (ev.resolution === 'approved' && resolvedItem) {
        // 审批已通过后，这些 id 已不再是“可由拒绝事件撤销的临时占位”。
        // 真正的花名册落定紧随 agents_created；先在这里摘掉清理资格，防乱序旧事件误删。
        resolvedItem.provisionalMemberIds = [];
      }
      if (ev.resolution === 'rejected' && resolvedItem) {
        const teamCard = resolvedItem.card as { proposed?: { id: string }[] };
        if (Array.isArray(teamCard.proposed) && teamCard.proposed.length) {
          const proposedIds = new Set(
            teamCard.proposed
              .map((p) => (typeof p.id === 'string' ? p.id : ''))
              .filter((id): id is string => id.length > 0),
          );
          const provisionalIds = new Set(resolvedItem.provisionalMemberIds || []);
          const removable = new Set(
            [...provisionalIds].filter((id) => proposedIds.has(id)),
          );
          if (removable.size) {
            c.members = (c.members || []).filter((m) => !removable.has(m.id));
          }
          resolvedItem.provisionalMemberIds = [];
        }
      }
      break;
    }

    // ── agents_created：花名册更新 ─────────────────────────
    case 'agents_created': {
      const members = ev.members || [];
      const committedIds = new Set(
        members
          .map((m) => (typeof m.id === 'string' ? m.id : ''))
          .filter((id): id is string => id.length > 0),
      );
      if (committedIds.size) {
        for (const item of c.items) {
          if (item.kind !== 'approval' || !item.provisionalMemberIds?.length) continue;
          item.provisionalMemberIds = item.provisionalMemberIds
            .filter((id) => !committedIds.has(id));
        }
      }
      for (const m of members) {
        // [v0.9c] name 是后端掷的（「林知远」），随事件一起来 —— 前端照单全收
        // [v0.9d] active=true：这是花名册事件 → 被归档的人会在这里**复活**
        registerMember(c, m.id, agents, roleTypes, m.role,
                       (m as { name?: string }).name, true);
      }
      const names = members
        .map((a) => displayInfo(c, a.id, agents, roleTypes).name || a.id)
        .join('、');
      c.items.push({ kind: 'system', text: names + i18n.t('state.17'), level: 'info' });
      break;
    }

    // ── [v0.9b] agent_removed：成员被归档 ────────────────────
    case 'agent_removed': {
      const target = ev.target_id;
      registerMember(c, target, agents, roleTypes);   // 保证他在册（历史气泡要认得他）
      const gone = c.members.find((x) => x.id === target);
      if (gone) {
        gone.status = 'removed';
        gone.state = 'idle';                          // 归档的人不该还在"忙"
        gone.busySince = undefined;
        gone.activeScopes = undefined;
      }
      const who = displayInfo(c, target, agents, roleTypes).name || target;
      c.items.push({ kind: 'system', text: who + i18n.t('state.21'), level: 'info' });
      break;
    }

    // ── instruction_injected：成员收到任务 ──────────────────
    case 'instruction_injected': {
      c.members = c.members || [];
      registerMember(c, ev.target_id, agents, roleTypes);
      c.items.push({
        kind: 'system',
        text: (displayInfo(c, ev.target_id, agents, roleTypes).name || ev.target_id) + i18n.t('state.20'),
        level: 'info',
      });
      break;
    }

    // ── report_submitted：成员提交报告 ──────────────────────
    case 'report_submitted': {
      c.members = c.members || [];
      // Submission is not an idle declaration.  agent_idle is the only event that
      // clears the member's working state.
      /*
       * [v0.30 Bug8] 失败报告不说「已提交报告」。
       *
       *   引擎的失败漏斗（fail_open_task）复用 report_submitted 事件收头像
       *   （契约不加新事件类型），report_hash 以 `failed-` 开头是它的记号。
       *   老代码对两种报告一视同仁地写「XX 已提交报告」——任务明明炸了，
       *   系统行却在报喜，用户更糊涂了。
       *   失败的**解释**（谁、哪件活、为什么）由引擎紧随的 error 事件给出
       *   （红色系统行）；这里只负责不添乱：失败 → 这条「已提交报告」不写。
       */
      const failed = typeof ev.report_hash === 'string'
        && ev.report_hash.startsWith('failed-');
      if (!failed) {
        c.items.push({
          kind: 'system',
          text: (displayInfo(c, ev.agent_id, agents, roleTypes).name || ev.agent_id) + i18n.t('state.19'),
          level: 'info',
        });
      }
      break;
    }

    // ── recovery_notice：恢复通知 ──────────────────────────
    case 'recovery_notice':
      c.items.push({
        kind: 'system',
        text: ev.message || i18n.t('state.30'),
        level: 'info',
      });
      break;

    // ── state_snapshot：完整重建（§2.3-e） ────────────────
    case 'state_snapshot': {
      c.banner = null;
      c.items = [];

      /*
       * ★ [v0.8e #1] **花名册不清空。**
       *
       *   这行 `c.members = []` 就是「开机头像先是文字、过一会儿才变宫格」的真凶。
       *
       *   时间线：握手时 project_created 带着 members 到了（v0.8d），左栏第一帧就是宫格；
       *   250ms 后 syncAllProjects 给每个项目要一份快照 → 快照落地 → **这里把人清空** →
       *   左栏瞬间退回文字头像 → 再从 conversation 重放的 agents_created 里一个个加回来。
       *   用户看到的那一下闪，是我们自己把人删了再加回去。
       *
       *   更糟的是：万一 agents_created 已经被 ring 淘汰（老项目），
       *   重放里根本没有它 —— 人就**再也回不来了**，头像永久退化成文字。
       *
       *   为什么当初要清：怕花名册里留着服务端已经没有的人。但花名册现在有**权威来源**了
       *   （project_created.members，后端从磁盘温载的那份）。快照是用来重建**消息流**的，
       *   不是用来重建人的。registerMember 本来就幂等，重放里的 agents_created
       *   只会补人、不会加重复。
       */
      // ★ [v0.7 #1] draft 不动。快照重建的是**服务端的历史**，
      //   而草稿是用户此刻手上还没发出去的字——重同步不该把它吃掉。

      /*
       * ★ [v0.10b Bug7] 重放前，先把每个成员的**权威 status** 记下来。
       *
       *   c.members 来自 project_created.members —— 后端从磁盘温载的那份，是真相
       *   （load_roster 建在 load_roster_full 之上，「末行为准」再滤归档；项目经理也是靠
       *   同一份数据认得队里有谁）。归档 → 重新加回的成员，末行是 active，这里就是 active。
       *
       *   下面重放 conversation 是为了重建**消息流**（「XX 已加入 / 已离开」这些系统行）。
       *   但重放里的 agent_removed / agents_created 会**顺手改 status**：一旦历史事件有缺口
       *   或次序问题——比如「加回」那条 agents_created 已被环形缓冲淘汰、窗口里只剩下更早的
       *   agent_removed——成员就会被重放**停在错误的 removed 上**。这正是「归档成员加回后
       *   前端仍显示已归档、而后端认为他在队里」的根：前后端读了同一份数据、却得出不同结论。
       *
       *   status 的权威在 project_created.members，**不在重放**。所以：重放照跑（重建消息流），
       *   跑完把**重放前就在册的**成员 status 复位到权威值；重放里新冒出来的人
       *   （例如只在历史里出现过、早已归档的成员）不在快照里，保持重放给出的状态、不动。
       */
      const authStatus = new Map(c.members.map((m) => [m.id, m.status]));
      const authRuntime = new Map(c.members.map((m) => [m.id, {
        state: m.state, busySince: m.busySince, activeScopes: m.activeScopes,
      }]));

      const convEvents = (ev.conversation || []) as InboundEvent[];
      for (const ce of convEvents) {
        try {
          applyEvent(c, ce, agents, roleTypes, { protectTransientFrame: false });
        } catch (replayErr) {
          console.error('[snapshot replay] event error', (ce as Record<string, unknown>).type, replayErr);
        }
      }
      // 复位：重放前就在册的成员，status 以权威来源（project_created.members）为准。
      for (const m of c.members) {
        const s = authStatus.get(m.id);
        if (s !== undefined) m.status = s;
        const runtime = authRuntime.get(m.id);
        if (runtime) {
          m.state = runtime.state;
          m.busySince = runtime.busySince;
          m.activeScopes = runtime.activeScopes;
        }
      }

      /*
       * [v1.0.24.4] 权威活动账本优先于本地运行时。
       *
       * 上面的复位是「没账本时」的老行为（保住本地运行态）。后端这次把账本
       * 附在快照里了，就以账本为准整体校准——长断线里丢掉的 agent_idle，
       * 在这里一次性抹平，忙碌态不再永久残留。旧后端不带 activity 字段 →
       * ev.activity 为 undefined → 完全退回老行为。
       */
      if (ev.activity) {
        calibrateRosterActivity(c, ev.activity);
      }

      if (typeof window !== 'undefined' && window.recordFriction) {
        window.recordFriction('B-7', 'snapshot rebuild executed', {
          conversation_len: convEvents.length,
          rebuilt_items: c.items.length,
        });
      }
      break;
    }

    case 'replay_complete':
      if (typeof ev.unread_count === 'number') c.unread = ev.unread_count;
      break;

    // ── error 事件：进全局通知通道 ─────────────────────────
    case 'error': {
      // 无 project_id → 服务器级 error，在 store 层进 notices
      // 有 project_id → 引擎级 error，进会话流
      const hasProject = !!(ev as Record<string, unknown>).project_id;
      if (hasProject) {
        c.items.push({
          kind: 'system',
          // [v0.40.2 #1] 聊天回复也是 LLM 调用：余额不足时把英文/402 换成中文人性化提示。
          text: humanizeLlmError(ev.message),
          level: 'error',
        });
      }

      /*
       * [v0.8e #4 → v0.30 Bug4] ★ 出错了 → **出事的那个人**的回合结束了 →
       *   把**他的**「正在输入」气泡定格。
       *
       *   thinking 会挂一个空的 streaming 气泡（见上），而收尾靠的是 message。
       *   一旦这一轮以 error 收场（引擎不会再发 message），那三个点就会**永远跳下去**。
       *   转个不停的光标是在撒谎：它说"他还在写"，其实他早就倒下了。
       *
       *   ★ 只定格 error 点名的那个人（ev.agent_id）。v0.29 之后错误是**局部的**：
       *     项目经理那轮炸了、或一条反馈调整失败，后台 Worker 还在好好干活——
       *     把他的状态气泡一并冻掉就是 Bug 4 的另一半。
       *     没点名的 error（比如反馈失败）不属于任何 agent 的回合，谁的气泡都不动；
       *     每个 agent 自己的回合收尾（message / agent_idle）负责收自己的摊。
       *
       *   定格之后：text 为空的气泡会被 ChatStream 过滤掉（空气泡守卫），
       *   已经流出半截的则原样留在屏幕上——那半截是真的发生过的。
       */
      const errAgent = (ev as { agent_id?: string }).agent_id;
      if (errAgent) {
        const identity = activityIdentity(c, ev);
        const stageState = (ev as unknown as { stage_state?: unknown }).stage_state;
        finishAgentStages(c, errAgent, identity, stageState === 'cancelled' ? 'cancelled' : 'error');
        settleAgentStreaming(c, errAgent, identity);
      }
      // 服务器级 error 由 store.handleEvent 处理（进 notices）
      break;
    }

    // ── 瞬时事件（无状态突变） ──────────────────────────────
    /*
     * ── [v0.8e #4] agent_thinking：**当场挂一个「正在输入」的气泡** ──
     *
     *   以前这里什么都不做：模型在想、在调工具、在憋一段长回复，屏幕上一片死寂，
     *   直到第一个 token 蹦出来。现在一收到 thinking 就把气泡占上（text 为空，streaming），
     *   StreamBubble 渲染成「正在输入 …」的三个点。
     *
     *   气泡里的 text 照旧由 stream_delta 累积——**只是不渲染**（见 StreamBubble）。
     *   为什么还要留着：message 事件的 content 有可能是空串（被打断的回合），
     *   那时候唯一还剩下的东西就是这段累积的文字。扔了它，用户就什么都看不到了。
     */
    case 'agent_thinking': {
      registerMember(c, ev.agent_id, agents, roleTypes);
      // Phase information only.  agent_active owns availability; the ensure call is a
      // compatibility path for old recordings that did not yet contain agent_active.
      const item = ensureActivityBubble(
        c, ev.agent_id, activityIdentity(c, ev), ev, protectTransientFrame,
      );
      appendObservableStage(
        stagesOf(item),
        ev as unknown as ObservableStageEvent,
        'plan',
        STAGE_MAX,
      );
      break;
    }

    /*
     * ── [v0.23 问题二] tool_gen / tool_complete：**把「它在干什么」挂到气泡上** ──
     *
     *   现象：Worker 干四十秒长活，屏幕上只有「正在输入」和三个点。用户不知道它在干嘛，
     *   也不知道它是不是卡死了，只能等。
     *
     *   ★ 这三个事件**后端一直在发**（engine._new_agent 里的 tool_gen_callback），
     *     契约里也早就定义好了（ToolGenSchema 带 tool_name）——只有这里，
     *     四个 case 并排 `break`，全扔了。数据一直在门口，没人开门。
     *
     *   tool_gen(tool_name) → 记在那个 agent 正在流的气泡上
     *   tool_complete       → 擦掉（一次调用结束，下一个还没来）
     *   message             → streaming=false，整条气泡换成 MessageBubble，活动行自然消失
     *                         （PRD：「最终回复出来后中间过程消失」）
     *
     *   为什么挂在**气泡**上而不是另起一个 conv 级的 map：气泡本来就是「这个 agent
     *   此刻的临时态」，它 streaming=false 的那一刻，活动行就该跟着没。挂在气泡上，
     *   这件事自动成立，不用再写一处清理逻辑（少一处清理 = 少一个忘记清理的 bug）。
     */
    case 'tool_gen': {
      registerMember(c, ev.agent_id, agents, roleTypes);
      const identity = activityIdentity(c, ev);
      const tool = (ev as { tool_name?: string }).tool_name || '';
      if (!tool) break;
      /*
       * [v0.30 Bug4] ★ 状态气泡是**自愈**的。
       *
       *   tool_gen 到达 = 这个人此刻确凿地在干活（这是他自己回合里发出的事件，
       *   不是猜的）。可他的流式气泡可能已经不在了：被落定的卡冻掉过（旧数据 /
       *   兜底路径）、或者被一张插进来的卡挡在上面（blockedAfter —— 再往里写
       *   就会画到卡的上方去）。
       *
       *   老代码在这两种情况下直接 break —— 活动行没地方挂，状态气泡就此失踪，
       *   直到下一条 stream_delta 才复活（长工具调用期间可能几十秒没有 delta）。
       *   现在：没有可用气泡 → **当场在流的末尾起一个新的**，活动行接着叠。
       *   状态气泡从此只依赖他自己的事件（thinking / tool_gen / message /
       *   agent_idle），卡片的弹出与落定都影响不了它——重构原则 5 的落点。
       */
      let gIdx = findLastStreamingIndex(c.items, ev.agent_id, identity);
      if (gIdx >= 0 && blockedAfter(c.items, gIdx)) {
        settleStreamingItem(c.items[gIdx] as AgentItem);   // 被卡挡住的旧气泡定格
        gIdx = -1;
      }
      if (gIdx < 0) {
        c.items.push({
          kind: 'agent',
          agentId: ev.agent_id,
          scopeId: identity.scopeId,
          channelId: identity.channelId,
          text: '',
          streaming: true,
          ...(protectTransientFrame ? { transientFrame: newTransientFrame() } : {}),
          ts: eventMillis(ev),
          seq: eventSeq(ev),
        });
        gIdx = c.items.length - 1;
      }
      const item = c.items[gIdx] as AgentItem;
      armTransientFrame(item, protectTransientFrame);
      const list = item.activities ?? (item.activities = []);
      // [v0.44.5] 参数详情的配对/原地细化是纯函数，见 toolActivity.ts。
      appendToolActivity(list, tool, ACTIVITY_MAX);
      appendObservableStage(
        stagesOf(item),
        ev as unknown as ObservableStageEvent,
        'plan',
        STAGE_MAX,
      );
      break;
    }

    /*
     * tool_complete：**什么都不做**。
     *
     * v0.23 在这里把活动擦掉了——因为那时候只有一行，擦了才能显示下一个。
     * 现在是叠加的：擦掉等于把「它刚才干了什么」从用户眼前抹走，而那正是他想看的。
     * 一条活动的谢幕时机只有一个：**message 到达、气泡落定**，整栈一起消失。
     */
    case 'tool_complete': {
      const identity = activityIdentity(c, ev);
      finishAgentStages(c, ev.agent_id, identity, 'complete');
      break;
    }
    case 'tool_start':
    case 'tool_call':
    case 'project_created':
      break;

    default:
      // 未知事件：console.warn + 忽略（不抛异常）
      console.warn('[state] unknown event type:', (ev as Record<string, unknown>).type);
      break;
  }
}

/**
 * [v0.23.1 问题五] 活动栈最多留几条。
 *
 * 一个 Worker 跑四十个工具调用是**正常**的（v0.22 就是这么设计的）。四十行活动会把
 * 气泡撑到整屏，PRD 要的「高度平滑过渡」也就无从谈起。留最近 6 条：
 * 用户想知道的是「它现在在干嘛、刚才在干嘛」，不是一份完整工单流水。
 */
const ACTIVITY_MAX = 6;
const STAGE_MAX = 6;
let transientFrameSerial = 0;

// ═══════════════════════════════════════════════════════════════
// 六、多会话管理
// ═══════════════════════════════════════════════════════════════

export function getConv(
  convs: Record<string, Conv>,
  projectId: string,
): Conv {
  if (!convs[projectId]) {
    convs[projectId] = {
      projectId,
      members: [],
      items: [],
      banner: null,
      draft: '',            // [v0.7 #1] 每个会话自带一个草稿槽，默认空串
      unread: 0,            // [v0.8d #5]
    };
  }
  const c = convs[projectId] as Conv;
  // 老会话（快照 / 落盘回灌）可能没有这些字段 —— 补上，别让组件读到 undefined
  if (typeof c.draft !== 'string') c.draft = '';
  if (typeof c.unread !== 'number') c.unread = 0;
  return c;
}

export function registerProject(
  convs: Record<string, Conv>,
  projectId: string,
  projectName?: string,
  projectDir?: string,
): Conv {
  const c = getConv(convs, projectId);
  c.projectName = projectName || projectId;
  if (projectDir) c.projectDir = projectDir;   // [v0.7 A0]
  return c;
}

/** [v0.7 #1] 草稿：写 / 清。纯函数，store 的 action 只是壳。 */
export function setDraft(convs: Record<string, Conv>, projectId: string, text: string): void {
  getConv(convs, projectId).draft = text;
}

export function clearDraft(convs: Record<string, Conv>, projectId: string): void {
  getConv(convs, projectId).draft = '';
}

export function getProjectList(convs: Record<string, Conv>): { project_id: string; name: string }[] {
  return Object.keys(convs).map((pid) => ({
    project_id: pid,
    name: convs[pid]?.projectName || pid,
  }));
}

// ═══════════════════════════════════════════════════════════════
// 七、内部工具
// ═══════════════════════════════════════════════════════════════

/**
 * [v0.7b #4] 卡片上方那截已经定格的话，如果 final 里又原样念了一遍，就切掉。
 *
 *   为什么需要这道保险：`message` 事件带的 `final_response` 到底是「最后一轮的话」
 *   还是「整轮的全文」，取决于 knowe_core 的 AgentLoop 怎么攒 —— 前端不该去赌。
 *     · 只是最后一轮 → 这里 startsWith 匹配不上，原样返回，什么也没发生。
 *     · 是整轮全文   → 卡片上方那截会被重复念一遍（旧气泡一段、新气泡里又一段），
 *                      在这里切掉。
 *
 *   只在**本轮里出现过审批卡**时才动手（本轮 = 最后一条用户消息之后）。
 *   没有卡的普通对话一个字都不碰 —— 别为了修一个 bug 去动一条本来就对的路。
 *
 * @param upto 收尾的那个气泡在 items 里的下标（要新建气泡就传 items.length）
 */
function dedupeFinal(
  items: Item[],
  agentId: string,
  identity: ActivityIdentity,
  content: string,
  upto: number,
): string {
  if (!content) return content;

  // 本轮从哪儿开始：最后一条用户消息之后
  let turnStart = 0;
  for (let i = upto - 1; i >= 0; i--) {
    if (items[i]?.kind === 'user') { turnStart = i + 1; break; }
  }

  let sawCard = false;
  let prefix = '';
  for (let i = turnStart; i < upto; i++) {
    const it = items[i];
    if (!it) continue;
    if (it.kind === 'approval') sawCard = true;
    if (it.kind === 'agent' && itemMatchesActivity(it, agentId, identity) && !it.streaming) {
      prefix += it.text;
    }
  }
  if (!sawCard || !prefix) return content;

  return content.startsWith(prefix)
    ? content.slice(prefix.length).replace(/^\s+/, '')
    : content;
}

/**
 * [v0.36] 从 message 事件里取出本轮产出的文件，稳妥地。
 *
 * schema 已经把 files 校验成合法的 ProducedFile[]（或缺省），这里只做一层运行时兜底：
 * 不是数组一律当空、过滤掉没有 path 的残缺项——绝不让一个畸形元素把渲染搞崩。
 */
type CompletionStatusLike = {
  completion_id?: unknown;
  agent_id?: unknown;
  status?: unknown;
  terminal?: unknown;
  reason?: unknown;
  gaps?: unknown;
  gap_details?: unknown;
  next_actions?: unknown;
  files?: unknown;
  version?: unknown;
  task_id?: unknown;
  attempt_id?: unknown;
  run_id?: unknown;
  scope_id?: unknown;
  channel_id?: unknown;
  seq?: unknown;
  ts?: unknown;
};

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

// [v1.0.23.3] message 落定时把推理字段写进气泡（事件无推理字段则不动）
function applyReasoningFields(item: AgentItem, ev: Record<string, unknown>): void {
  const reasoning = optionalString(ev.reasoning);
  if (reasoning) {
    item.reasoning = reasoning;
    const seconds = ev.reasoning_seconds;
    if (typeof seconds === 'number' && Number.isFinite(seconds)) {
      item.reasoningSeconds = seconds;
    }
  }
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => optionalString(item)).filter((item): item is string => !!item);
}

function booleanOrUndefined(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

function completionVersionOf(value: { version?: unknown; seq?: unknown }): number {
  // Sequence is transport order, not Completion version. Treating seq as version makes
  // an unversioned status that arrives later outrank an already-visible legacy message.
  return normalizeCompletionVersion(value.version);
}

function completionItemVersion(item: Item | undefined): number {
  if (!item || (item.kind !== 'agent' && item.kind !== 'system')) return -1;
  return item.completionVersion ?? 0;
}

function completionItemClock(item: Item | undefined): { version: number; authority: CompletionAuthority } | undefined {
  if (!item || (item.kind !== 'agent' && item.kind !== 'system')) return undefined;
  return {
    version: completionItemVersion(item),
    authority: item.completionAuthority || 'completion_status',
  };
}

function completionMessageMeta(
  event: { status?: unknown; terminal?: unknown; version?: unknown; seq?: unknown },
  completionId: string,
): Pick<AgentItem, 'completionId' | 'completionStatus' | 'completionVersion' | 'completionAuthority' | 'completionTerminal' | 'completionTransient'> {
  return {
    completionId,
    completionStatus: optionalString(event.status),
    completionVersion: completionVersionOf(event),
    completionAuthority: 'message',
    completionTerminal: booleanOrUndefined(event.terminal),
    completionTransient: false,
  };
}

function findCompletionItemIndex(items: Item[], completionId: string): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if ((item?.kind === 'agent' || item?.kind === 'system') && item.completionId === completionId) {
      return i;
    }
  }
  return -1;
}

function removeDuplicateCompletionItems(items: Item[], completionId: string, keepIndex: number): void {
  for (let i = items.length - 1; i >= 0; i--) {
    if (i === keepIndex) continue;
    const item = items[i];
    if ((item?.kind === 'agent' || item?.kind === 'system') && item.completionId === completionId) {
      items.splice(i, 1);
      if (i < keepIndex) keepIndex -= 1;
    }
  }
}

function newTransientFrame(): TransientFrameGuard {
  transientFrameSerial += 1;
  return {
    id: `tf_${Date.now().toString(36)}_${transientFrameSerial.toString(36)}`,
    painted: false,
    settlePending: false,
  };
}

function armTransientFrame(item: AgentItem, enabled: boolean): void {
  if (enabled && !item.transientFrame) item.transientFrame = newTransientFrame();
}

/**
 * 权威终态可以立即写入 item，但过程视图至少要完成一次 paint。
 * 无 guard（历史/静态 item）或已 paint 的 guard 仍按原语义立即定格。
 */
function settleStreamingItem(item: AgentItem): void {
  if (!item.streaming) return;
  const guard = item.transientFrame;
  if (guard && !guard.painted) {
    guard.settlePending = true;
    return;
  }
  item.streaming = false;
  delete item.transientFrame;
  // [v1.0.23.3 修订] 落定 morph 标记：仅实时流式定格时设置（回放/快照无
  //   streaming item，走不到这里）→ ChatStream 据此给气泡挂 morph-in 动画。
  item.morphIn = true;
}

/** StreamBubble 在首帧后回执；Store 的双帧兜底也复用同一入口。 */
export function acknowledgeTransientFrame(c: Conv, frameId: string): boolean {
  for (const item of c.items) {
    if (item.kind !== 'agent' || item.transientFrame?.id !== frameId) continue;
    item.transientFrame.painted = true;
    if (item.transientFrame.settlePending) {
      item.streaming = false;
      delete item.transientFrame;
    }
    return true;
  }
  return false;
}

/** 仅返回已收到终态、仍在等待首帧的 guard，避免后台会话永久悬挂。 */
export function pendingTransientFrameIds(c: Conv): string[] {
  const ids: string[] = [];
  for (const item of c.items) {
    if (item.kind === 'agent'
        && item.transientFrame
        && item.transientFrame.settlePending
        && !item.transientFrame.painted) {
      ids.push(item.transientFrame.id);
    }
  }
  return ids;
}

function stagesOf(item: AgentItem): WorkStageLine[] {
  return item.stages ?? (item.stages = []);
}

function finishMessageStages(item: AgentItem, event: { status?: unknown }): void {
  const status = optionalString(event.status);
  if (status === 'WAITING') {
    finishWorkStages(stagesOf(item), 'waiting', 'wait', undefined, STAGE_MAX);
  } else if (status === 'CANCELLED' || status === 'SUPERSEDED' || status === 'ROLLED_BACK') {
    finishWorkStages(stagesOf(item), 'cancelled', undefined, undefined, STAGE_MAX);
  } else if (status && !['SUCCEEDED', 'PARTIAL'].includes(status)) {
    finishWorkStages(stagesOf(item), 'error', undefined, undefined, STAGE_MAX);
  } else {
    finishWorkStages(stagesOf(item), 'complete', 'deliver', undefined, STAGE_MAX);
  }
}

function finishAgentStages(
  c: Conv,
  agentId: string,
  identity: ActivityIdentity | undefined,
  state: WorkStageLine['state'],
  terminalStage?: WorkStageLine['stage'],
  detail?: string,
): void {
  for (const item of c.items) {
    if (item.kind !== 'agent'
        || item.agentId !== agentId
        || (identity && !itemMatchesActivity(item, agentId, identity))
        || !item.streaming) continue;
    const last = item.stages?.[item.stages.length - 1];
    if (state === 'complete'
        && last
        && (last.state === 'error' || last.state === 'cancelled' || last.state === 'waiting')) {
      continue;
    }
    finishWorkStages(stagesOf(item), state, terminalStage, detail, STAGE_MAX);
  }
}

function settleAgentStreaming(
  c: Conv,
  agentId: string,
  identity?: ActivityIdentity,
): void {
  for (const item of c.items) {
    if (item.kind === 'agent'
        && item.agentId === agentId
        && (!identity || itemMatchesActivity(item, agentId, identity))
        && item.streaming) {
      settleStreamingItem(item);
    }
  }
}

function humanizeCompletionStatus(event: CompletionStatusLike): string {
  const status = optionalString(event.status) || 'FAILED';
  const reason = optionalString(event.reason);
  const gaps = stringArray(event.gaps);
  const actions = stringArray(event.next_actions);
  const details = Array.isArray(event.gap_details) ? event.gap_details : [];
  const detailMessage = details
    .map((item) => item && typeof item === 'object'
      ? optionalString((item as { message?: unknown }).message)
      : undefined)
    .find((item): item is string => !!item);
  const detailAction = details
    .map((item) => item && typeof item === 'object'
      ? optionalString((item as { repair_action?: unknown }).repair_action)
      : undefined)
    .find((item): item is string => !!item);
  const message = detailMessage || gaps[0] || reason;
  const action = detailAction || actions[0];
  const withAction = (base: string): string => action ? i18n.t('state.nextAction', { base, action }) : base;
  switch (status) {
    case 'SUCCEEDED': return i18n.t('state.10');
    case 'PARTIAL': return withAction(message ? i18n.t('state.delivered', { message }) : i18n.t('state.18'));
    case 'WAITING': return withAction(message ? i18n.t('state.waiting', { message }) : i18n.t('state.35'));
    case 'BLOCKED': return withAction(message ? i18n.t('state.blocked', { message }) : i18n.t('state.08'));
    case 'CANCELLED': return message ? i18n.t('state.cancelled', { message }) : i18n.t('state.05');
    case 'TIMED_OUT': return withAction(message ? i18n.t('state.timedOut', { message }) : i18n.t('state.07'));
    case 'ROLLED_BACK': return withAction(message ? i18n.t('state.rolledBack', { message }) : i18n.t('state.23'));
    case 'SUPERSEDED': return message ? i18n.t('state.superseded', { message }) : i18n.t('state.06');
    case 'SYSTEM_ERROR': return withAction(message ? i18n.t('state.systemError', { message }) : i18n.t('state.29'));
    default: return withAction(message ? i18n.t('state.failed', { message }) : i18n.t('state.09'));
  }
}

function finishCompletionStages(item: AgentItem, status: string): void {
  if (status === 'WAITING') {
    finishWorkStages(stagesOf(item), 'waiting', 'wait', undefined, STAGE_MAX);
  } else if (status === 'SUCCEEDED' || status === 'PARTIAL') {
    finishWorkStages(stagesOf(item), 'complete', 'deliver', undefined, STAGE_MAX);
  } else if (status === 'CANCELLED' || status === 'SUPERSEDED' || status === 'ROLLED_BACK') {
    finishWorkStages(stagesOf(item), 'cancelled', undefined, undefined, STAGE_MAX);
  } else {
    finishWorkStages(stagesOf(item), 'error', undefined, undefined, STAGE_MAX);
  }
}

function applyCompletionStatus(
  c: Conv,
  event: CompletionStatusLike,
  agents: AgentRegistry | null,
  roleTypes: RoleType[] | null,
  protectTransientFrame: boolean,
): void {
  const completionId = optionalString(event.completion_id);
  const agentId = optionalString(event.agent_id) || 'coordinator';
  if (!completionId) return;
  registerMember(c, agentId, agents, roleTypes);
  const identity = activityIdentity(c, event);
  const incomingVersion = completionVersionOf(event);
  const index = findCompletionItemIndex(c.items, completionId);
  const existing = index >= 0 ? c.items[index] : undefined;
  if (!shouldAcceptCompletionMetadata(completionItemVersion(existing), incomingVersion)) return;
  const mayReplaceText = shouldReplaceCompletionProjection(
    completionItemClock(existing),
    { version: incomingVersion, authority: 'completion_status' },
  );

  const status = optionalString(event.status) || 'FAILED';
  const terminal = booleanOrUndefined(event.terminal) ?? status !== 'WAITING';
  const files = producedFilesOf(event);
  const current = index >= 0 && existing?.kind === 'agent'
    ? existing as AgentItem
    : undefined;
  const item: AgentItem = current || {
    kind: 'agent',
    agentId,
    scopeId: identity.scopeId,
    channelId: identity.channelId,
    text: '',
    streaming: false,
  };
  const wasStreaming = item.streaming === true;
  let shouldRemainStreaming = false;
  item.agentId = agentId;
  item.scopeId = identity.scopeId;
  item.channelId = identity.channelId;
  if (mayReplaceText) {
    item.text = humanizeCompletionStatus(event);
    shouldRemainStreaming = !terminal && status !== 'WAITING';
    if (shouldRemainStreaming) {
      item.streaming = true;
      armTransientFrame(item, protectTransientFrame);
    }
    item.completionAuthority = 'completion_status';
    item.completionTransient = status === 'SUCCEEDED' || status === 'PARTIAL';
  } else {
    // [v1.0.13][R3 P0] Same-version status can enrich metadata, but never
    // roll a visible Worker message back to a mechanical placeholder.
    shouldRemainStreaming = false;
  }
  finishCompletionStages(item, status);
  if (!shouldRemainStreaming) {
    if (wasStreaming) settleStreamingItem(item);
    else item.streaming = false;
  }
  item.completionId = completionId;
  item.completionStatus = status;
  item.completionVersion = incomingVersion;
  item.completionTerminal = terminal;
  item.completionGaps = stringArray(event.gaps);
  item.completionNextActions = stringArray(event.next_actions);
  item.ts = typeof event.ts === 'string' ? Date.parse(event.ts) || Date.now() : Date.now();
  item.seq = typeof event.seq === 'number' ? event.seq : item.seq;
  if (files.length) item.files = files;
  if (index >= 0) c.items.splice(index, 1, item);
  else c.items.push(item);
  removeDuplicateCompletionItems(c.items, completionId, index >= 0 ? index : c.items.length - 1);

  if (terminal) {
    // Completion/WAITING/error state describes the task, not the member's availability.
    // Keep the member working until an explicit agent_idle event arrives.
    settleAgentStreaming(c, agentId, identity);
  }
}

type CompletionViewV1Like = {
  completion_id?: unknown;
  agent_id?: unknown;
  status?: unknown;
  terminal?: unknown;
  version?: unknown;
  task_id?: unknown;
  attempt_id?: unknown;
  run_id?: unknown;
  scope_id?: unknown;
  channel_id?: unknown;
  rendered_text?: unknown;
  user_visible?: unknown;
  seq?: unknown;
  ts?: unknown;
};

function applyCompletionViewV1(
  c: Conv,
  event: CompletionViewV1Like,
  agents: AgentRegistry | null,
  roleTypes: RoleType[] | null,
): void {
  const completionId = optionalString(event.completion_id);
  const agentId = optionalString(event.agent_id) || 'coordinator';
  const renderedText = typeof event.rendered_text === 'string' ? event.rendered_text.trim() : '';
  if (!completionId || !renderedText) return;
  registerMember(c, agentId, agents, roleTypes);
  const identity = activityIdentity(c, event);

  const incomingVersion = completionVersionOf(event);
  const index = findCompletionItemIndex(c.items, completionId);
  const existing = index >= 0 ? c.items[index] : undefined;
  if (!shouldReplaceCompletionProjection(
    completionItemClock(existing),
    { version: incomingVersion, authority: 'completion_view_v1' },
  )) return;

  const userVisible = event.user_visible && typeof event.user_visible === 'object'
    ? event.user_visible as { artifacts?: unknown; gaps?: unknown; next_actions?: unknown }
    : {};
  const files = producedFilesOf({ files: userVisible.artifacts });
  const status = optionalString(event.status) || 'FAILED';
  const terminal = booleanOrUndefined(event.terminal) ?? status !== 'WAITING';
  const current = existing?.kind === 'agent' ? existing as AgentItem : undefined;
  const item: AgentItem = current || {
    kind: 'agent', agentId,
    scopeId: identity.scopeId, channelId: identity.channelId,
    text: '', streaming: false,
  };
  const wasStreaming = item.streaming === true;
  item.agentId = agentId;
  item.scopeId = identity.scopeId;
  item.channelId = identity.channelId;
  item.text = renderedText;
  item.completionId = completionId;
  item.completionStatus = status;
  item.completionVersion = incomingVersion;
  item.completionAuthority = 'completion_view_v1';
  item.completionTerminal = terminal;
  item.completionTransient = false;
  item.completionGaps = stringArray(userVisible.gaps);
  item.completionNextActions = stringArray(userVisible.next_actions);
  item.ts = typeof event.ts === 'string' ? Date.parse(event.ts) || Date.now() : Date.now();
  item.seq = typeof event.seq === 'number' ? event.seq : item.seq;
  if (files.length) item.files = files;
  else delete item.files;
  finishCompletionStages(item, status);
  // [v1.0.23.5] view_v1 是权威终态投影，事件自带 reasoning 时写入气泡
  applyReasoningFields(item, event as unknown as Record<string, unknown>);
  if (wasStreaming) settleStreamingItem(item);
  else item.streaming = false;

  if (index >= 0) c.items.splice(index, 1, item);
  else c.items.push(item);
  removeDuplicateCompletionItems(c.items, completionId, index >= 0 ? index : c.items.length - 1);
  if (terminal) settleAgentStreaming(c, agentId, identity);
}

function producedFilesOf(ev: { files?: unknown }): ProducedFile[] {
  const raw = ev.files;
  if (!Array.isArray(raw)) return [];
  const files: ProducedFile[] = [];
  for (const candidate of raw) {
    if (!candidate || typeof candidate !== 'object') continue;
    const path = optionalString((candidate as { path?: unknown }).path);
    if (!path) continue;
    const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '');
    const name = optionalString((candidate as { name?: unknown }).name)
      || normalized.split('/').filter(Boolean).pop()
      || path;
    files.push({ ...(candidate as Record<string, unknown>), path, name } as ProducedFile);
  }
  return files;
}

function ensureActivityBubble(
  c: Conv,
  agentId: string,
  identity: ActivityIdentity,
  ev: InboundEvent | undefined,
  protectTransientFrame: boolean,
): AgentItem {
  let index = findLastStreamingIndex(c.items, agentId, identity);
  if (index >= 0 && !blockedAfter(c.items, index)) {
    const existing = c.items[index] as AgentItem;
    armTransientFrame(existing, protectTransientFrame);
    return existing;
  }
  if (index >= 0) settleStreamingItem(c.items[index] as AgentItem);

  const item: AgentItem = {
    kind: 'agent',
    agentId,
    scopeId: identity.scopeId,
    channelId: identity.channelId,
    text: '',
    streaming: true,
    ...(protectTransientFrame ? { transientFrame: newTransientFrame() } : {}),
    ...(ev ? { ts: eventMillis(ev), seq: eventSeq(ev) } : {}),
  };
  c.items.push(item);
  index = c.items.length - 1;
  return c.items[index] as AgentItem;
}

/** [v0.7b #4] 这个 agent 最后一个「还在流」的匹配 scope 气泡下标（没有 → -1） */
function findLastStreamingIndex(
  items: Item[],
  agentId: string,
  identity?: ActivityIdentity,
): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it && it.kind === 'agent'
        && it.agentId === agentId
        && (!identity || itemMatchesActivity(it, agentId, identity))
        && it.streaming
        && !it.transientFrame?.settlePending) return i;
  }
  return -1;
}

/**
 * [v0.7b #4] 第 idx 个气泡后面，有没有别人插进来过？
 *
 *   审批卡、系统行（「XX 已加入项目」「XX 已收到任务」）一旦插在气泡后面，
 *   这个气泡在屏幕上就已经「过去了」——再往里追加文字，就是往历史里塞新话。
 *
 *   从尾巴往前扫到 idx 为止：绝大多数情况下气泡就是最后一条，循环一次就出来。
 *   （stream_delta 一秒钟来几十条，这里不能是 O(n) 的 slice + some。）
 *   走下标不走对象同一性 —— immer 的 draft 代理不该被拿来做 === 比较。
 */
function blockedAfter(items: Item[], idx: number): boolean {
  if (idx < 0) return false;
  for (let i = items.length - 1; i > idx; i--) {
    const it = items[i];
    if (it && (it.kind === 'approval' || it.kind === 'system')) return true;
  }
  return false;
}

/**
 * 通用的「从后往前找」。
 *
 * [v0.7b #4] 流式那两处已经改用 findLastStreamingIndex（要的是下标，不是对象）——
 * 这个函数留着：它是通用工具，别的地方还会用；删了只是给下一个人添麻烦。
 */
export function findLast<T extends Item>(
  arr: Item[],
  predicate: (el: T) => boolean,
): T | undefined {
  for (let i = arr.length - 1; i >= 0; i--) {
    const el = arr[i];
    if (el && predicate(el as T)) return el as T;
  }
  return undefined;
}

// ═══════════════════════════════════════════════════════════════
// [v0.40.0] 聊天右键菜单 / 多选 / 引用 / 翻译 —— 新类型与条目定位
// ═══════════════════════════════════════════════════════════════

/**
 * 一条消息在**本条会话视图里**的稳定钥匙。右键菜单、多选、翻译块、视图删除、
 * 收藏跳回，认的都是它——四处一把钥匙，不许各配各的。
 *
 * 取法（优先级从高到低）：
 *   1. seq —— 事件序号，跨快照重建稳定（回放回来还是同一把）。
 *   2. 用户乐观气泡还没有 seq → 用 cmid（回声确认前它就是这条消息的身份证）。
 *   3. 都没有（极老的历史/系统行）→ 退回数组下标。下标在快照重建后会漂，
 *      但这类条目本来就不进收藏、不进翻译（右键只开在 user/agent 气泡上），
 *      漂了也只影响一次会话内的多选——可接受，且诚实。
 */
export function itemKeyOf(it: Item, index: number): string {
  if (it.kind === 'user') {
    return it.seq != null ? `s${it.seq}` : `c${it.cmid}`;
  }
  if (it.kind === 'agent') {
    return it.seq != null ? `s${it.seq}` : `i${index}`;
  }
  if (it.kind === 'approval') return `a${it.cardId}`;
  return `i${index}`;
}

/** [v0.40.0] 输入框上方的引用条（右键「引用」）。ref = 被引消息的 itemKey（可跳回）。 */
export interface ChatQuote {
  /** 被引消息的发送者显示名（「我」/ Agent 显示名）。 */
  name: string;
  /** 被引正文（引用条里最多显示一行，存全文由 Composer 截断）。 */
  text: string;
  /** 所在会话 + 条目钥匙——将来做「点引用条跳回原文」时用；后端暂无结构化引用字段。 */
  projectId: string;
  itemKey: string;
}
