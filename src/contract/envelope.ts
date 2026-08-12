/** [v1.0.13][R3] Atomic CompletionViewV1 schema and message idempotency. */
/**
 * IPC Envelope v2 — zod 契约代码审计版
 *
 * 真源：Claude 审计报告（handoff_v0.2.2/0_计划书/
 *       Knowe_v0.3_前端重做与联调_分析报告与施工计划.md）第 2 章
 * 方法：以 server.py / engine.py / gate.py / tools_knowe.py / recovery.py
 *       的实际发射与分发代码为唯一真源，逐事件抽取真实字段
 *
 * 铁律：
 *   - 新事件先进此文件再写代码
 *   - 字段名以此文件为准（杜绝 text/content 漂移家族）
 *   - 入站事件必经 zod 校验；校验失败 = console.error + 丢弃 + 走廊计数
 *   - 类型通过 z.infer 派生，不手写 TypeScript 类型
 *   - 契约歧义时以本审计报告为唯一裁决依据
 *
 * version: 2
 * changelog:
 *   v2 (2026-07-12): Claude 代码审计全面修正——以下每项均标注审计报告行号
 *     [#1]  §2.1#9  🔴 ResolutionSchema 枚举 expired→timeout（后端从不发 expired；
 *                     timeout 被静默丢弃是审批超时的根因）
 *     [#2]  §2.1#14 🔴 RecoveryNoticeSchema.details: z.string()→z.object({}).passthrough()
 *                     （后端发的是对象不是字符串，当前拓扑休眠但 B2 持久化一开就炸）
 *     [#3]  §2.1#16 🔴 ErrorEventSchema: project_id/seq 必填→optional
 *                     （服务器级 error 无信封字段）
 *     [#4]  §2.1#8  🟡 ApprovalCardSchema: card_id/agent_id 改 optional；
 *                     tool 放宽为 union(ApprovalToolEnum, z.string())
 *                     （崩溃恢复复提路径缺三层字段）
 *     [#5]  §2.1#7  🟠 ProposeAgentsCardSchema / ProposeNextCardSchema 内层：
 *                     补 card_id: optional（历史遗留假 ID，禁止读取）+
 *                     recovered: optional（复提卡标记）
 *     [#6]  §2.1#18 🟠 StateSnapshotSchema.agents: MemberSchema[]→z.array(z.unknown())
 *                     （活跃路径恒空但引擎级快照形状不同；花名册以 conversation
 *                     重放结果为准）
 *     [#7]  §2.2#2  🔴 ApproveCmdSchema / RejectCmdSchema: project_id optional→必填
 *                     （缺省落 demo 导致发错引擎）
 *     [#8]  §2.2#5  🔴 RequestSnapshotCmdSchema: 加 project_id 必填
 *                     （v0.2 没有这个字段，resync 永远只重建 demo）
 *     [#9]  §2.2#1  🟠 UserMessageCmdSchema: client_msg_id optional→必填
 *                     （乐观渲染与回声哨兵都靠它）
 *     [#10] §2.2#7  🟠 ShutdownCmdSchema: 保留 schema 但加 @deprecated 注释
 *                     （前端永不发送；误发=自杀）
 *     [#11] §2.1#19 🟡 ReplayCompleteSchema: 三分支已兼容（project_id/note optional 均已覆盖
 *                     超时分支无 project_id 的情况）
 *     [#12] §2.1#3  🟠 MessageSchema.content 可为空串（中断回合 final_response=""；
 *                     provider 报错文案也走此事件）——schema 原样不拒；状态层规则见 PROTOCOL.md
 *     [#13] §2.1#6  ✅ ToolCallSchema: 保留 schema，标注 dormant（后端从不发射）
 *     [#14] 结构  🔴 InboundEventSchema 补入 StateSnapshot / ReplayComplete /
 *                     ResyncRequired / Pong 四个定义但未入联合类型的事件
 *                     （v1 定义了 schema 却未接入校验通路，四个事件类型全被拒收）
 *   v1 (2026-07-11): 初版 — 从 protocol_spec.md 全量生成，与 vanilla envelope.ts 字段一致
 */

import { z } from 'zod';

// ═══════════════════════════════════════════════════════════════
// 一、通用基础类型
// ═══════════════════════════════════════════════════════════════

/** 项目唯一标识（会话 id = project_id） */
export type ProjectId = string;

/** Agent 唯一标识（格式：project_id::local_id） */
export type AgentId = string;

/** 审批卡唯一标识（= approval_id 值；统一使用顶层 card_id） */
export type CardId = string;

/** Optional identity carried by all events that belong to one visible execution. */
export const ActivityCorrelationSchema = z.object({
  scope_id: z.string().optional(),
  task_id: z.string().optional(),
  attempt_id: z.string().optional(),
  run_id: z.string().optional(),
  completion_id: z.string().optional(),
  channel_id: z.string().optional(),
});
export type ActivityCorrelation = z.infer<typeof ActivityCorrelationSchema>;

/** Live-only public work-stage hints carried by existing observable events. */
export const WorkStageSchema = z.enum([
  'explore', 'integrate', 'plan', 'implement', 'verify', 'review', 'deliver', 'wait',
]);
export const WorkStageStateSchema = z.enum([
  'active', 'complete', 'error', 'cancelled', 'waiting',
]);
export const VisibleStageSchema = z.object({
  phase: z.string().optional(),
  stage: WorkStageSchema.optional(),
  stage_detail: z.string().optional(),
  stage_state: WorkStageStateSchema.optional(),
});

/**
 * 审批结果枚举 — 代码审计版
 *
 * 后端实际发射面（gate.py + engine.py 逐路径核对）：
 *   approved / rejected / timeout / cancelled
 *
 * ⚠️ v1 的 'expired' 已被移除——后端从不发射此值；
 *   'timeout' 是 worker 侧超时解决的真实枚举。
 * [v2#1] §2.1#9
 */
export const ResolutionSchema = z.enum([
  'approved',
  'rejected',
  'timeout',
  'cancelled',
]);
export type Resolution = z.infer<typeof ResolutionSchema>;

/**
 * 审批工具类型（正常路径）
 * [v2#4] §2.1#8: 崩溃恢复复提路径 tool 可能为 "?" 或其他未知值，
 *   业务层使用放宽的版本 ApprovalToolWideSchema
 */
export const ApprovalToolEnumSchema = z.enum(['propose_agents', 'propose_next']);
export type ApprovalToolEnum = z.infer<typeof ApprovalToolEnumSchema>;

/** 审批工具类型（放宽版：兼容复提路径未知 tool 值） */
export const ApprovalToolWideSchema = z.union([
  ApprovalToolEnumSchema,
  z.string(),
]);
export type ApprovalToolWide = z.infer<typeof ApprovalToolWideSchema>;

// ═══════════════════════════════════════════════════════════════
// 二、审批卡数据形状
// ═══════════════════════════════════════════════════════════════

/**
 * 组队审批卡（propose_agents）内部数据
 *
 * [v2#5] §2.1#7: 补 card_id（历史遗留假 ID，与顶层 card_id 不同值，
 *   审批一律用顶层 card_id，此字段仅用于 schema 兼容，禁止业务读取）
 *   补 recovered（崩溃恢复复提卡标记）
 */
export const ProposeAgentsCardSchema = z.object({
  status: z.literal('pending_approval'),
  expires_at: z.string(), // ISO 8601 UTC
  proposed: z.array(z.object({
    role: z.string(),
    id: z.string(),
    /** [v0.9c] 后端算好的名字 —— 卡上就该显示他将来的名字，而不是一个占位符 */
    name: z.string().optional(),
  })),
  approval_id: z.string(),
  /** 历史遗留假 ID（≠ 顶层 card_id），禁止业务读取 */
  card_id: z.string().optional(),
  /** 崩溃恢复复提标记 */
  recovered: z.boolean().optional(),
});
export type ProposeAgentsCard = z.infer<typeof ProposeAgentsCardSchema>;

/**
 * 派活审批卡（propose_next）内部数据
 *
 * [v2#5] §2.1#7: 同 ProposeAgentsCardSchema，补 card_id + recovered
 */
export const ProposeNextCardSchema = z.object({
  status: z.literal('pending_approval'),
  expires_at: z.string(), // ISO 8601 UTC
  target_id: z.string(),
  instruction: z.string(),
  approval_id: z.string(),
  /** 历史遗留假 ID（≠ 顶层 card_id），禁止业务读取 */
  card_id: z.string().optional(),
  /** 崩溃恢复复提标记 */
  recovered: z.boolean().optional(),
  /**
   * [v1.0.24.3] 审批期间用户【我有新意见】的每轮原文（后端 adjust_instruction 累积）。
   *   非空 = 这张卡被用户改过 → 卡面显示「已修改」badge。
   *   optional：老后端 / 没改过的卡没有这个字段。
   */
  feedback_history: z.array(z.string()).optional(),
});
export type ProposeNextCard = z.infer<typeof ProposeNextCardSchema>;

/**
 * [v0.5] 建群审批卡（create_project）内部数据 —— 知知专用。
 *
 * v0.4 时这种卡还不存在，所以知知只能在对话里口头确认项目名。
 * 现在它进了契约（后端 contract.py 的 _check_card 也同步认了这一种），
 * 于是建群也走审批：卡上带一个待确认的项目名，**用户还能改**，改完点确认才建。
 */
export const ProposeProjectCardSchema = z.object({
  status: z.literal('pending_approval'),
  expires_at: z.string(), // ISO 8601 UTC
  /** 知知提议的项目名——用户可以在卡上改掉 */
  project_name: z.string(),
  /**
   * [v0.7 A0] 项目目录。
   *
   * 知知**不填这个**——她是个模型，没资格替用户在磁盘上指一个地方。
   * 卡上会有一个「选择目录」按钮，路径由屏幕前的人挑；确认时前端把它随
   * create_project 指令一起发给后端。字段留在契约里是为了向前兼容
   * （将来若有别的来源带着目录提议建群，卡能原样承载）。
   */
  project_dir: z.string().optional(),
  approval_id: z.string(),
  /** 历史遗留假 ID（≠ 顶层 card_id），禁止业务读取 */
  card_id: z.string().optional(),
  /** 崩溃恢复复提标记 */
  recovered: z.boolean().optional(),
});
export type ProposeProjectCard = z.infer<typeof ProposeProjectCardSchema>;

/**
 * [v0.9b] 移除成员审批卡（propose_remove_agent）内部数据。
 *
 * ★ 和派活卡的区别只有一个：**没有 instruction**。
 *   所以联合类型里它必须排在 ProposeNextCardSchema **后面**——
 *   zod 的 union 是「第一个过的就算」，把它排前面，派活卡会被它先接住
 *   （target_id 有了、instruction 是多余字段被剥掉），
 *   于是屏幕上会弹出一张「要移除 fe_1 吗」的卡——而项目经理本来是想派活。
 */
export const ProposeRemoveCardSchema = z.object({
  status: z.literal('pending_approval'),
  expires_at: z.string(), // ISO 8601 UTC
  target_id: z.string(),
  /** 移除原因（项目经理填的，可能没有） */
  reason: z.string().optional(),
  approval_id: z.string(),
  /** 历史遗留假 ID（≠ 顶层 card_id），禁止业务读取 */
  card_id: z.string().optional(),
  /** 崩溃恢复复提标记 */
  recovered: z.boolean().optional(),
});
export type ProposeRemoveCard = z.infer<typeof ProposeRemoveCardSchema>;

/** 审批卡联合类型 */
export const ApprovalCardDataSchema = z.union([
  ProposeAgentsCardSchema,
  ProposeNextCardSchema,        // ★ 必须在移除卡之前（它多一个 instruction，更"具体"）
  ProposeRemoveCardSchema,
  ProposeProjectCardSchema,
]);
export type ApprovalCardData = z.infer<typeof ApprovalCardDataSchema>;

// ═══════════════════════════════════════════════════════════════
// 三、成员结构
// ═══════════════════════════════════════════════════════════════

/** 成员信息（来自 agents_created / project_created.members） */
export const MemberSchema = z.object({
  id: z.string(),
  role: z.string(),
  /**
   * [v0.9c] ★ 后端算好的名字（「前端 1」）。
   *
   *   名字是**元数据**，和角色、头像一样归后端管、进花名册、跟着项目走。
   *   在此之前它是前端 registerMember() 里随机掷出来的——App 一重启就换一个人名，
   *   而「认人」正是名字唯一的用处。
   *
   *   optional：老后端不发这个字段，前端要能退回确定性的 displayInfo 名字，不能炸。
   */
  name: z.string().optional(),
  display_name: z.string().optional(),
  status: z.enum(['idle', 'busy']).optional(),
});
export type Member = z.infer<typeof MemberSchema>;

// ═══════════════════════════════════════════════════════════════
// 四、入站事件（引擎/服务器 → 前端）
// ═══════════════════════════════════════════════════════════════

// ── 4.1 聊天/流式 ──

/** Actor visible execution starts. */
export const AgentActiveSchema = z.object({
  type: z.literal('agent_active'),
  agent_id: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  reason: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type AgentActive = z.infer<typeof AgentActiveSchema>;

/** Agent 开始思考 */
export const AgentThinkingSchema = z.object({
  type: z.literal('agent_thinking'),
  agent_id: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(), // ISO 8601 UTC
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
  ...VisibleStageSchema.shape,
});
export type AgentThinking = z.infer<typeof AgentThinkingSchema>;

/** 流式增量文本（字段名 content，非 text） */
export const StreamDeltaSchema = z.object({
  type: z.literal('stream_delta'),
  agent_id: z.string(),
  content: z.string(), // ← 注意：是 content，不是 text
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type StreamDelta = z.infer<typeof StreamDeltaSchema>;

/** [v1.0.23.3] 推理增量（reasoning_content 透传；同 stream_delta 瞬时，不落盘） */
export const ReasoningDeltaSchema = z.object({
  type: z.literal('reasoning_delta'),
  agent_id: z.string(),
  content: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type ReasoningDelta = z.infer<typeof ReasoningDeltaSchema>;

/** [v1.0.23.3] 四方向建议（辅助 LLM 提取；瞬时事件，不落盘） */
export const SuggestionsSchema = z.object({
  type: z.literal('suggestions'),
  agent_id: z.string(),
  items: z.array(z.object({
    title: z.string(),
    sub: z.string().optional().default(''),
  })),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type Suggestions = z.infer<typeof SuggestionsSchema>;

/** [v0.10b] 引擎判定项目经理谎言 → 清空当前流式气泡 → 重跑纠正 */
export const StreamResetSchema = z.object({
  type: z.literal('stream_reset'),
  agent_id: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type StreamReset = z.infer<typeof StreamResetSchema>;

/**
 * [v0.36] 本轮 Worker 产出的一个文件（safe_write_file / copy_external_file 的产物）。
 *
 * 后端在 message 事件上捎带一个数组（见下 MessageSchema.files），前端据此在气泡下方
 * 渲染文件卡片、点开预览。
 *
 * ★ **passthrough + 尽量宽松**：这是被塞进 discriminatedUnion 的 message 里的字段，
 *   一旦某个元素校验失败，validateInbound 会把**整条 message 丢掉**（气泡都没了）。
 *   所以只硬性要求 path/name（取文件与显示的命根子），其余全 optional，且放行未知字段——
 *   宁可多带几个前端不认的字段，也绝不因为多一个字段把整条消息拒收。
 */
function producedFileBasename(path: string): string {
  const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '');
  const basename = normalized.split('/').filter(Boolean).pop();
  return basename || path || 'file';
}

const ProducedFileWireSchema = z.object({
  /** 项目内相对路径。既是列表 key，也是前端向 /preview 取文件用的 path 参数。 */
  path: z.string().trim().min(1),
  /** 文件名可由生产者显式给出；旧/V2 投影缺省时从 path 安全推导。 */
  name: z.string().trim().min(1).optional(),
  /** 扩展名，小写不带点（如 'md' / 'pdf'）。前端拿不到 kind 时按它兜底分类。 */
  ext: z.string().optional(),
  /** 预览大类（markdown/html/image/pdf/docx/pptx/sheet/file）——后端算好，前端直接用。 */
  kind: z.string().optional(),
  /** 字节数（stat 拿到才有），用于降级卡展示与「过大」判断。 */
  bytes: z.number().nonnegative().optional(),
  /** 最后修改时间 ISO 8601（stat 拿到才有）。 */
  mtime: z.string().optional(),
  /** Completion ArtifactManifest / 预览层的兼容元数据。 */
  disposition: z.string().optional(),
  sha256: z.string().optional(),
  identity: z.string().optional(),
  media_type: z.string().optional(),
  preview_url: z.string().optional(),
}).passthrough();

/**
 * 唯一 ProducedFile DTO：path 是唯一硬要求；name 总能在边界处得到。
 * 单个附件异常不得拖垮正文或其他卡片。
 */
export const ProducedFileSchema = ProducedFileWireSchema.transform((file: z.infer<typeof ProducedFileWireSchema>) => ({
  ...file,
  name: file.name || producedFileBasename(file.path),
}));
export type ProducedFile = z.infer<typeof ProducedFileSchema>;

export const ProducedFilesSchema = z.preprocess((raw: unknown) => {
  if (!Array.isArray(raw)) return raw;
  const valid: unknown[] = [];
  raw.forEach((item, index) => {
    const parsed = ProducedFileSchema.safeParse(item);
    if (parsed.success) {
      valid.push(parsed.data);
    } else {
      console.warn('[contract] 忽略畸形文件附件', { index, issues: parsed.error.issues });
    }
  });
  return valid;
}, z.array(ProducedFileSchema));

/**
 * 完整消息文本（流式以 message 收尾）
 *
 * [v2#12] §2.1#3: content 可为空串（中断回合 final_response=""；
 *   provider 报错文案也走此事件）。schema 不拒空串——状态层规则见 PROTOCOL.md §c
 *
 * [v0.36] files：本轮产出的文件清单（可选）。**不改变 content 正文** —— 它是纯附载，
 *   气泡照常渲染 content，文件卡片挂在气泡下方。空/缺省都合法（绝大多数消息没有文件）。
 */
export const MessageSchema = z.object({
  type: z.literal('message'),
  agent_id: z.string(),
  content: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  files: ProducedFilesSchema.optional(),
  event_id: z.string().optional(),
  idempotency_key: z.string().optional(),
  ...ActivityCorrelationSchema.shape,
  status: z.string().optional(),
  terminal: z.boolean().optional(),
  reason: z.string().optional(),
  gaps: z.array(z.string()).optional(),
  gap_details: z.array(z.object({}).passthrough()).optional(),
  next_actions: z.array(z.string()).optional(),
  version: z.number().int().nonnegative().optional(),
  delivery: z.object({}).passthrough().optional(),
  metadata: z.object({}).passthrough().optional(),
  ...VisibleStageSchema.shape,
}).passthrough();
export type Message = z.infer<typeof MessageSchema>;

/**
 * 工具生成提示。
 *
 * tool_name 契约保持不变；可选阶段字段只承载由真实工具事件推导出的 live UI 投影。
 * v0.44.5 后端可兼容编码 `函数名␟紧凑详情`，旧后端继续发送纯函数名。
 */
export const ToolGenSchema = z.object({
  type: z.literal('tool_gen'),
  agent_id: z.string(),
  tool_name: z.string(), // ← 仍是 tool_name，不新增协议字段
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
  ...VisibleStageSchema.shape,
});
export type ToolGen = z.infer<typeof ToolGenSchema>;

/** 工具开始执行 */
export const ToolStartSchema = z.object({
  type: z.literal('tool_start'),
  agent_id: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type ToolStart = z.infer<typeof ToolStartSchema>;

/** 工具执行完成 */
export const ToolCompleteSchema = z.object({
  type: z.literal('tool_complete'),
  agent_id: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type ToolComplete = z.infer<typeof ToolCompleteSchema>;

/**
 * 工具调用详情
 *
 * [v2#13] §2.1#6: 契约定义但后端从不发射——dormant 状态保留
 *   以备未来实现，字段全部 optional 防止误拒
 */
export const ToolCallSchema = z.object({
  type: z.literal('tool_call'),
  agent_id: z.string().optional(),
  name: z.string().optional(),
  args: z.unknown().optional(),
  project_id: z.string().optional(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
});
export type ToolCall = z.infer<typeof ToolCallSchema>;

// ── 4.2 闸门/审批 ──

/**
 * 审批卡事件
 *
 * 铁律：审批一律用顶层 card_id（= card.approval_id）
 * card 内层的 card_id 是历史遗留假 ID，禁止读取
 *
 * [v2#4] §2.1#8: card_id / agent_id 改 optional（崩溃恢复复提路径缺三层字段）；
 *   tool 改用放宽版 ApprovalToolWideSchema
 */
export const ApprovalCardSchema = z.object({
  type: z.literal('approval_card'),
  agent_id: z.string().optional(),     // [v2#4] 复提路径可能缺失
  tool: ApprovalToolWideSchema,        // [v2#4] 复提路径 tool 可能为 "?"
  card_id: z.string().optional(),     // [v2#4] 复提路径可能缺失；归一化层回填 card.approval_id
  card: ApprovalCardDataSchema,
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
});
export type ApprovalCard = z.infer<typeof ApprovalCardSchema>;

/**
 * 审批已解决（广播）
 *
 * [v2#1] §2.1#9: resolution 枚举 expired→timeout
 * 状态层规则：首个解决为准幂等处理；timeout+cancelled 先后到达只取先到者
 */
export const ApprovalResolvedSchema = z.object({
  type: z.literal('approval_resolved'),
  card_id: z.string(),
  resolution: ResolutionSchema,
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
});
export type ApprovalResolved = z.infer<typeof ApprovalResolvedSchema>;

// ── 4.3 团队/状态 ──

/** 成员已创建（组队通过后） */
export const AgentsCreatedSchema = z.object({
  type: z.literal('agents_created'),
  agent_id: z.string(),
  count: z.number().int().min(0),
  members: z.array(MemberSchema),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
});
export type AgentsCreated = z.infer<typeof AgentsCreatedSchema>;

/**
 * [v0.9b] 成员被归档（**不是彻底删除**）。
 *
 * 他不再接新任务；但他交过的报告、写过的文件、收到过的指令，一个字都没动——
 * 那是用户的资产。前端要做的只有两件事：把他从宫格里摘下来，在时间线上留一句
 * 「XX 已离开项目」。
 */
export const AgentRemovedSchema = z.object({
  type: z.literal('agent_removed'),
  /** 谁提的（项目经理） */
  agent_id: z.string(),
  /** 被归档的那个人 */
  target_id: z.string(),
  reason: z.string().optional(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
});
export type AgentRemoved = z.infer<typeof AgentRemovedSchema>;

/** 指令已注入（成员收到任务） */
export const InstructionInjectedSchema = z.object({
  type: z.literal('instruction_injected'),
  agent_id: z.string(),
  target_id: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type InstructionInjected = z.infer<typeof InstructionInjectedSchema>;

/** 报告已提交（成员完成任务） */
export const ReportSubmittedSchema = z.object({
  type: z.literal('report_submitted'),
  agent_id: z.string(),
  report_hash: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type ReportSubmitted = z.infer<typeof ReportSubmittedSchema>;

/** [v0.15] Worker 回合结束——不在 STRUCTURAL，不进时间线/快照，仅驱动前端 busy→idle */
export const AgentIdleSchema = z.object({
  type: z.literal('agent_idle'),
  agent_id: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
  ...ActivityCorrelationSchema.shape,
});
export type AgentIdle = z.infer<typeof AgentIdleSchema>;

// ── 4.4 项目/生命周期 ──

/**
 * [v0.13 卡片] 目录待处理信息（随 project_created 捎来的内层对象）。
 *
 * 后端在握手/冷启动补发 project_created 时，若该项目当前目录失效，就带上这个对象。
 * 前端据此在侧边栏亮红字「未处理事项」，并允许用同一个 request_id 重开目录恢复卡片——
 * 不必再等用户先发一条消息去触发 project_directory_required。
 *
 * 字段与 ProjectDirectoryRequiredSchema 的子集对齐（顶层 project_id/project_name 已在
 * project_created 里，故此处只带这三样）。
 */
export const ProjectDirectoryInfoSchema = z.object({
  previous_dir: z.string(),
  reason: z.string(),
  request_id: z.string(),
});
export type ProjectDirectoryInfo = z.infer<typeof ProjectDirectoryInfoSchema>;

/**
 * 项目已创建（服务器级事件）
 *
 * 无 seq 事件白名单成员——传输层旁路，不进水位/去重/gap 逻辑
 */
export const ProjectCreatedSchema = z.object({
  type: z.literal('project_created'),
  project_id: z.string(),
  project_name: z.string().optional(),
  seq: z.number().int().min(0).optional(),

  /**
   * [v0.8d #1] ★ 这个项目的花名册，**随握手一起到**。
   *
   *   在此之前，前端要知道群里有谁，只有一条路：把整条会话的快照要过来，
   *   从重放的 agents_created 里重建。20 个群就是 20 次往返 —— 打开软件之后，
   *   头像一个一个往外蹦，蹦三秒。
   *
   *   可后端**早就知道**这些人是谁（wake_projects 温载花名册，就在内存里）。
   *   它一直没说，只是因为没有一个字段可以说。现在有了。
   *
   *   零往返：project_created 本来就是握手时每个项目发一条的。
   *   捎上这一个数组，头像在第一帧就是对的。
   */
  members: z.array(MemberSchema).optional(),

  /** 后端一直在发这两个字段，只是契约里没写 → 被 zod 静默剥掉了。补上。 */
  unread_count: z.number().int().min(0).optional(),
  project_dir: z.string().optional(),

  /**
   * [v0.13 卡片] 该项目当前目录失效时，握手/冷启动随第一帧带上待处理信息。
   *   ⚠ 必须在 schema 里声明，否则 zod 的 .object() 会把这个未知字段**静默剥掉**，
   *     App 永远收不到 → 重连后侧边栏红字与卡片入口全丢。
   */
  directory_required: ProjectDirectoryInfoSchema.optional(),
});
export type ProjectCreated = z.infer<typeof ProjectCreatedSchema>;

/**
 * 恢复通知
 *
 * [v2#2] §2.1#14: details 改 z.object({}).passthrough().optional()
 *   后端发送的是对象 {agents_restored, reports_restored, history_messages,
 *   stale_approvals_count}，不是字符串
 */
export const RecoveryNoticeSchema = z.object({
  type: z.literal('recovery_notice'),
  message: z.string(),
  details: z.object({}).passthrough().optional(), // [v2#2] 对象型 details
  project_id: z.string(),
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
});
export type RecoveryNotice = z.infer<typeof RecoveryNoticeSchema>;

/**
 * 错误事件
 *
 * 同时覆盖引擎级 error（有 project_id/seq）和服务器级 error（无 project_id/seq）
 *
 * [v2#3] §2.1#16: project_id / seq 改 optional（服务器级 error 无信封）
 *   状态层：无 project_id 的错误进全局系统通知（toast/走廊），不进任何会话流
 */
export const ErrorEventSchema = z.object({
  type: z.literal('error'),
  message: z.string(),
  agent_id: z.string().optional(),
  project_id: z.string().optional(),  // [v2#3] 服务器级 error 无此字段
  project_name: z.string().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0).optional(), // [v2#3] 服务器级 error 无此字段
  ...ActivityCorrelationSchema.shape,
  ...VisibleStageSchema.shape,
});
export type ErrorEvent = z.infer<typeof ErrorEventSchema>;

// ── 4.5 用户回显 ──

/**
 * 用户消息回显（让用户发言进入可重建时间线）
 *
 * 回声哨兵信号源——user_echo 未在 5s 内到达 ⇒ 广播失聪 ⇒ 出声 + 自动重连
 */
/**
 * [v1.0.19.4] 用户附件在协议里的形状：只带**路径与身份 + 签名**，从不带字节。
 *   后端凭 sig（主进程用 runtime_token 对 path 的 HMAC）确认这条路径是用户亲手选进来的，
 *   再直读原文件、原样打包给 LLM（DESIGN 决策 #1/#2/#3/#9）。
 */
export const AttachmentInputSchema = z.object({
  path: z.string(),
  name: z.string(),
  ext: z.string().optional(),
  size: z.number().nonnegative().optional(),
  sig: z.string().optional(),
}).passthrough();
export type AttachmentInput = z.infer<typeof AttachmentInputSchema>;

/**
 * [v1.0.23.1] 转发结构化载荷（协议形状）：随 user_message 出站、随 user_echo 回传。
 *   content 字段 = 用户配言原文；原文与来源在 forwarded 里，LLM 模板由后端构造。
 */
export const ForwardedPayloadSchema = z.object({
  /** 「转发自 X」里的 X（原消息发送者显示名）。 */
  sourceName: z.string(),
  /** 来源群/项目名（知知/私聊来源可缺省）。 */
  sourceProjectName: z.string().optional(),
  /** 被转发消息完整原文（引用窗内容）。 */
  originalText: z.string(),
  /** 用户附言（气泡主文案；空串 = 无附言转发）。 */
  comment: z.string(),
  /** 原文是否按 Markdown 渲染（Agent 富文本 = true）。 */
  markdown: z.boolean().optional(),
  /** 源消息引用（可选，留作将来跳回/审计）。 */
  sourceRef: z.object({ projectId: z.string(), itemKey: z.string().optional() }).optional(),
});
export type ForwardedPayload = z.infer<typeof ForwardedPayloadSchema>;

export const UserEchoSchema = z.object({
  type: z.literal('user_echo'),
  content: z.string(),
  client_msg_id: z.string().nullable().optional(),
  project_id: z.string(),
  seq: z.number().int().min(0),
  /** [v1.0.19.4] 本条用户消息带的附件元数据（无字节）——气泡渲染文件卡、重放历史复原。 */
  attachments: z.array(AttachmentInputSchema).optional(),
  /** [v1.0.23.1] 转发结构化载荷（后端原样回传）——重放历史时前端据此恢复引用窗（修复 B4）。 */
  forwarded: ForwardedPayloadSchema.optional(),
});
export type UserEcho = z.infer<typeof UserEchoSchema>;

// ── 4.6 服务器级控制事件 ──

/**
 * 项目业务目录已失效，需要系统级目录选择弹窗。
 *
 * 无 seq、不中聊天时间线。传输层收到后应直接交给全局 modal/原生目录选择器，
 * 用户选好目录后发送 set_project_directory；取消/关闭则发送 cancel_project_directory。
 */
export const ProjectDirectoryRequiredSchema = z.object({
  type: z.literal('project_directory_required'),
  project_id: z.string(),
  project_name: z.string(),
  previous_dir: z.string(),
  reason: z.string(),
  request_id: z.string(),
  message: z.string().optional(),
  can_cancel: z.boolean().optional(),
});
export type ProjectDirectoryRequired = z.infer<typeof ProjectDirectoryRequiredSchema>;

/**
 * 用户已为隔离中的项目选择新目录，后端完成恢复。
 *
 * 无 seq、不中聊天时间线。系统级弹窗应按 request_id 关闭，并刷新项目目录状态。
 */
export const ProjectDirectoryRestoredSchema = z.object({
  type: z.literal('project_directory_restored'),
  project_id: z.string(),
  project_dir: z.string(),
  request_id: z.string(),
  message: z.string().optional(),
});
export type ProjectDirectoryRestored = z.infer<typeof ProjectDirectoryRestoredSchema>;


/**
 * 项目永久删除的瞬时阶段通知。
 *
 * 无 seq、不中聊天时间线、不持久化。operation_id 让同一个现有 Toast 原地更新，
 * elapsed_ms 只用于决定超过 2 秒后才显示，不提供虚假的百分比。
 */
export const ProjectDeleteProgressSchema = z.object({
  type: z.literal('project_delete_progress'),
  operation_id: z.string(),
  project_id: z.string(),
  phase: z.enum(['closing', 'staging', 'committing', 'cleanup']),
  message: z.string(),
  elapsed_ms: z.number().int().nonnegative(),
});
export type ProjectDeleteProgress = z.infer<typeof ProjectDeleteProgressSchema>;

/**
 * [v1.0.24.4] 权威活动账本条目（后端 engine.open_activity_snapshot 序列化结果）。
 *
 * 只含「正在干活」的条目——后端 _open_activity 只记 open 占用，没有显式 idle 项。
 * 因此前端校准语义是：**在账本里 → 忙；不在账本里 → 空闲**（空列表 = 全员空闲）。
 * 三个身份字段与前端 activityScopeKey(activityIdentity()) 拼键完全同构，保证校准写入的
 * 键与后续 agent_idle 事件要删除的键是同一个键——自愈闭环的命门。
 * started_at 为毫秒，供 busySince 排序。
 */
export const ActivityLedgerEntrySchema = z.object({
  agent_id: z.string(),
  scope_id: z.string(),
  channel_id: z.string(),
  started_at: z.number().int(),
});
export type ActivityLedgerEntry = z.infer<typeof ActivityLedgerEntrySchema>;

/**
 * 完整状态快照（长断线后整体重建）
 *
 * conversation 内事件携带原始 seq，重建时绕过水位/去重逐条 applyEvent
 *
 * [v2#6] §2.1#18: agents 放宽为 z.array(z.unknown())
 *   活跃路径 agents 恒空；引擎级快照 shape 不同（字段 agent_id 非 id、
 *   status 含 'working' 不在前端枚举）。
 *   花名册以 conversation 里重放的 agents_created 为准重建。
 */
export const StateSnapshotSchema = z.object({
  type: z.literal('state_snapshot'),
  project_id: z.string().optional(),
  last_seq: z.number().int().min(0),
  agents: z.array(z.unknown()),          // [v2#6] 放宽兼容两形状
  conversation: z.array(z.unknown()),    // UI 事件投影的白名单数组

  pending_card: z.unknown().nullable().optional(),
  seq: z.number().int().min(0),          // 快照本身消耗一个新 seq 并写入 ring
  /**
   * [v1.0.24.4] 该群引擎的权威活动账本（可选）。旧后端不带此字段 → 前端退回现状不校准。
   */
  activity: z.array(ActivityLedgerEntrySchema).optional(),
});
export type StateSnapshot = z.infer<typeof StateSnapshotSchema>;

/**
 * 回放完成
 *
 * 三分支：
 *   1) 有历史：{type, last_seq, project_id}
 *   2) 无可回放：{type, last_seq, note, project_id}
 *   3) 5 秒超时：{type, last_seq: 0}——无 project_id
 *
 * [v2#11] §2.1#19: project_id/note 已 optional，三分支全兼容
 */
export const ReplayCompleteSchema = z.object({
  type: z.literal('replay_complete'),
  last_seq: z.number().int().min(0),
  project_id: z.string().optional(),    // [v2#11] 超时分支无此字段
  note: z.string().optional(),          // [v2#11] 仅在无可回放分支出现
  unread_count: z.number().int().min(0).optional(),
  /**
   * [v1.0.24.4] 该群引擎的权威活动账本（可选）。旧后端不带此字段 → 前端退回现状不校准。
   */
  activity: z.array(ActivityLedgerEntrySchema).optional(),
});
export type ReplayComplete = z.infer<typeof ReplayCompleteSchema>;

/**
 * 需要重新同步
 *
 * 无 seq 事件白名单成员——仅出现在握手回放且 ring 已淘汰时
 * 前端用"本次握手的 project_id"作上下文
 */
export const ResyncRequiredSchema = z.object({
  type: z.literal('resync_required'),
  last_seq: z.number().int().min(0),
  message: z.string().optional(),
});
export type ResyncRequired = z.infer<typeof ResyncRequiredSchema>;

/**
 * 心跳回显
 *
 * 无 seq 事件白名单成员——不走水位/去重
 */
export const PongSchema = z.object({
  type: z.literal('pong'),
});
export type Pong = z.infer<typeof PongSchema>;

/** CompletionEvent 的即时、可重放展示状态。 */
export const CompletionStatusValueSchema = z.enum([
  'SUCCEEDED', 'PARTIAL', 'FAILED', 'BLOCKED', 'WAITING',
  'CANCELLED', 'TIMED_OUT', 'ROLLED_BACK', 'SUPERSEDED', 'SYSTEM_ERROR',
]);
export type CompletionStatusValue = z.infer<typeof CompletionStatusValueSchema>;

export const CompletionStatusSchema = z.object({
  type: z.literal('completion_status'),
  event_id: z.string(),
  completion_id: z.string(),
  task_id: z.string(),
  attempt_id: z.string(),
  run_id: z.string().optional(),
  scope_id: z.string().optional(),
  channel_id: z.string().optional(),
  agent_id: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  status: CompletionStatusValueSchema,
  terminal: z.boolean(),
  reason: z.string().default(''),
  gaps: z.array(z.string()).default([]),
  gap_details: z.array(z.object({}).passthrough()).default([]),
  next_actions: z.array(z.string()).default([]),
  files: ProducedFilesSchema.optional(),
  version: z.number().int().nonnegative().optional(),
  metadata: z.object({}).passthrough().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
}).passthrough();
export type CompletionStatusEvent = z.infer<typeof CompletionStatusSchema>;

/** [v1.0.13][R3] 经后端边界校验并确定性渲染的一次原子结果投影。 */
export const UserFacingCompletionV1Schema = z.object({
  summary: z.string().trim().min(1),
  artifacts: ProducedFilesSchema.default([]),
  verification: z.array(z.string()).default([]),
  risks: z.array(z.string()).default([]),
  gaps: z.array(z.string()).default([]),
  next_actions: z.array(z.string()).default([]),
}).passthrough();
export type UserFacingCompletionV1 = z.infer<typeof UserFacingCompletionV1Schema>;

export const CompletionViewV1Schema = z.object({
  type: z.literal('completion_view_v1'),
  event_id: z.string(),
  completion_id: z.string(),
  task_id: z.string(),
  attempt_id: z.string(),
  run_id: z.string().optional(),
  scope_id: z.string().optional(),
  channel_id: z.string().optional(),
  agent_id: z.string(),
  project_id: z.string(),
  project_name: z.string().optional(),
  version: z.number().int().positive(),
  status: CompletionStatusValueSchema,
  terminal: z.boolean(),
  user_visible: UserFacingCompletionV1Schema,
  rendered_text: z.string().trim().min(1),
  created_at: z.string(),
  delivery: z.object({}).passthrough().optional(),
  metadata: z.object({}).passthrough().optional(),
  ts: z.string().optional(),
  seq: z.number().int().min(0),
}).passthrough();
export type CompletionViewV1Event = z.infer<typeof CompletionViewV1Schema>;

// ── 4.7 入站事件联合类型 ──

/**
 * 所有入站事件（引擎/服务器 → 前端）
 *
 * [v2#14] 补入 StateSnapshot / ReplayComplete / ResyncRequired / Pong
 *   v1 定义了这四个事件的 schema 但未加入联合类型，导致校验通路拒收
 */
export const InboundEventSchema = z.discriminatedUnion('type', [
  // 聊天/流式
  AgentActiveSchema,
  AgentThinkingSchema,
  StreamDeltaSchema,
  ReasoningDeltaSchema,    // [v1.0.23.3]
  SuggestionsSchema,       // [v1.0.23.3]
  StreamResetSchema,       // [v0.10b] 纠正项目经理谎言前清空流式气泡
  MessageSchema,
  ToolGenSchema,
  ToolStartSchema,
  ToolCompleteSchema,
  ToolCallSchema,          // ⚠️ dormant——后端从不发射，保留以备未来实现
  // 闸门/审批
  ApprovalCardSchema,
  ApprovalResolvedSchema,
  // 团队/状态
  AgentsCreatedSchema,
  AgentRemovedSchema,          // [v0.9b]
  InstructionInjectedSchema,
  ReportSubmittedSchema,
  AgentIdleSchema,           // [v0.15] Worker 回合结束
  CompletionStatusSchema,    // legacy CompletionEvent 状态占位
  CompletionViewV1Schema,    // [v1.0.13] 原子用户结果投影
    // 项目/生命周期
  ProjectCreatedSchema,
  RecoveryNoticeSchema,
  ErrorEventSchema,
  // 用户回显
  UserEchoSchema,
  // 服务器级控制事件 [v2#14]
  ProjectDirectoryRequiredSchema,
  ProjectDirectoryRestoredSchema,
  ProjectDeleteProgressSchema,
  StateSnapshotSchema,
  ReplayCompleteSchema,
  ResyncRequiredSchema,
  PongSchema,
]);
export type InboundEvent = z.infer<typeof InboundEventSchema>;

// ═══════════════════════════════════════════════════════════════
// 五、出站指令（前端 → 服务端）
// ═══════════════════════════════════════════════════════════════

/**
 * 用户发送消息
 *
 * [v2#9] §2.2#1: client_msg_id 改必填——乐观渲染（pending 态定位）
 *   与回声哨兵（超时未确认判定失聪）都靠此字段
 */
export const UserMessageCmdSchema = z.object({
  type: z.literal('user_message'),
  project_id: z.string(),
  content: z.string(),
  client_msg_id: z.string(),         // [v2#9] 必填
  attachments: z.array(AttachmentInputSchema).optional(),  // [v1.0.19.4]
  // [v1.0.23.1] 转发结构化载荷（content = 用户配言原文；LLM 模板由后端构造）。
  forwarded: ForwardedPayloadSchema.optional(),
});
export type UserMessageCmd = z.infer<typeof UserMessageCmdSchema>;

/**
 * 审批确认
 *
 * [v2#7] §2.2#2: project_id 改必填
 *   project_id 必须取自卡片所属会话，禁止用 activeProjectId 兜底
 *   （v0.2 的兜底在跨项目切换时会发错引擎）
 */
export const ApproveCmdSchema = z.object({
  type: z.literal('approve'),
  project_id: z.string(),            // [v2#7] 必填
  approval_id: z.string(),
});
export type ApproveCmd = z.infer<typeof ApproveCmdSchema>;

/**
 * 审批拒绝
 *
 * [v2#7] §2.2#2: project_id 改必填（同 ApproveCmdSchema）
 */
export const RejectCmdSchema = z.object({
  type: z.literal('reject'),
  project_id: z.string(),            // [v2#7] 必填
  approval_id: z.string(),
});

/**
 * [v0.26] 「我有新意见」：**改卡面，不落定**。
 *
 * 它和 approve / reject **并列**——都是控制面：直达 gate，不经消息队列。
 *   · approve / reject → 把这张卡**落定**
 *   · feedback_instruction → 把这张卡的 instruction **换一版**（卡还在等，倒计时照走）
 *
 * v0.24 / v0.25 两版把它做成了「发一条聊天消息」，于是每次都要绕一大圈：
 * 作废旧卡 → 项目经理重开一个回合 → 重新提案。两次都在半路把用户的意见弄丢了。
 * 这一版把它放回它本来就该在的地方。
 */
export const FeedbackInstructionCmdSchema = z.object({
  type: z.literal('feedback_instruction'),
  project_id: z.string(),
  approval_id: z.string(),
  feedback: z.string(),
});
export type RejectCmd = z.infer<typeof RejectCmdSchema>;

/**
 * 创建项目
 *
 * [v0.7 A0] 新增 project_dir —— 项目目录（Worker 沙箱的根）。
 *   UI 上是**必填**的（NewProjectModal 两个字段都填了才让点创建），
 *   但契约里留成可选：老客户端、以及知知那条「后端用卡上的原名兜底建群」的路径
 *   可能不带它。后端收不到就落到默认的 data/workspaces/{project_id}/。
 */
export const CreateProjectCmdSchema = z.object({
  type: z.literal('create_project'),
  project_id: z.string(),
  project_name: z.string(),
  project_dir: z.string().optional(),
  /**
   * [主动拉入worker] 建群时用户勾选的职能前缀列表（如 ["gis","da","fe"]）。
   *   后端建群后按此列表逐个实例化 Worker 并拉入。缺省/空 = 不选，行为与旧版一致。
   */
  roles: z.array(z.string()).optional(),
});
export type CreateProjectCmd = z.infer<typeof CreateProjectCmdSchema>;

/**
 * [v1.0.23.4] 群聊中途添加 Agent 员工。
 *
 * roles：职能前缀数组，**允许重复**（同职能多选，如 ['fe','fe','gis']
 * = 加 2 个前端 + 1 个 GIS）。数量编码在数组长度里，后端 _next_agent_id
 * 自动编号（fe_1 占用 → fe_2/fe_3…），无上限。
 */
export const AddAgentsCmdSchema = z.object({
  type: z.literal('add_agents'),
  project_id: z.string(),
  roles: z.array(z.string()),
});
export type AddAgentsCmd = z.infer<typeof AddAgentsCmdSchema>;

/**
 * 请求事件回放
 *
 * project_id 必填（= 当前活跃项目，无项目时 'demo'）
 * since_seq = 该项目本地真实水位（新会话水位 0 时项目本无历史包袱）
 * 传输层封死：仅 onopen 首帧可发此指令
 */
export const ReplayRequestCmdSchema = z.object({
  type: z.literal('replay_request'),
  since_seq: z.number().int().min(0),
  project_id: z.string(),            // 必填
});
export type ReplayRequestCmd = z.infer<typeof ReplayRequestCmdSchema>;

/**
 * 请求完整快照
 *
 * [v2#8] §2.2#5: 加 project_id 必填
 *   v0.2 没有这个字段，resync 永远只重建 demo——多项目 resync 断腿的根源
 */
export const RequestSnapshotCmdSchema = z.object({
  type: z.literal('request_snapshot'),
  project_id: z.string(),            // [v2#8] 必填——根治多项目 resync 断腿
});
export type RequestSnapshotCmd = z.infer<typeof RequestSnapshotCmdSchema>;

/** 系统目录选择器选定新目录后回传。request_id 必须取自最新弹窗，防止旧弹窗覆盖新状态。 */
export const SetProjectDirectoryCmdSchema = z.object({
  type: z.literal('set_project_directory'),
  project_id: z.string(),
  project_dir: z.string(),
  request_id: z.string(),
  /**
   * [v0.13 卡片] 目录恢复卡允许「像初始建群一样」顺手改名。可选：不带则只换目录不改名。
   *   出站命令运行时不过 zod（sendRaw 直发），此字段仅供编译期类型对齐 OutboundCommand。
   */
  project_name: z.string().optional(),
});
export type SetProjectDirectoryCmd = z.infer<typeof SetProjectDirectoryCmdSchema>;

/** 用户取消或关闭系统目录选择器；项目继续保持隔离。 */
export const CancelProjectDirectoryCmdSchema = z.object({
  type: z.literal('cancel_project_directory'),
  project_id: z.string(),
  request_id: z.string().optional(),
});
export type CancelProjectDirectoryCmd = z.infer<typeof CancelProjectDirectoryCmdSchema>;

/** 心跳 ping */
export const PingCmdSchema = z.object({
  type: z.literal('ping'),
});
export type PingCmd = z.infer<typeof PingCmdSchema>;

/**
 * 关闭项目引擎
 *
 * [v2#10] §2.2#7: schema 保留但前端永不发送
 *   @deprecated 误发会停掉整个项目引擎（含线程退出），无 UI 需求
 */
export const ShutdownCmdSchema = z.object({
  type: z.literal('shutdown'),
  project_id: z.string(),
});
/** @deprecated 前端永不发送——误发=自杀 */
export type ShutdownCmd = z.infer<typeof ShutdownCmdSchema>;

/** 所有出站指令联合类型 */
export const OutboundCommandSchema = z.discriminatedUnion('type', [
  UserMessageCmdSchema,
  ApproveCmdSchema,
  RejectCmdSchema,
  FeedbackInstructionCmdSchema,
  CreateProjectCmdSchema,
  AddAgentsCmdSchema,
  ReplayRequestCmdSchema,
  RequestSnapshotCmdSchema,
  SetProjectDirectoryCmdSchema,
  CancelProjectDirectoryCmdSchema,
  PingCmdSchema,
  ShutdownCmdSchema,       // [v2#10] 保留但前端禁用
]);
export type OutboundCommand = z.infer<typeof OutboundCommandSchema>;

// ═══════════════════════════════════════════════════════════════
// 六、无 seq 事件白名单
// ═══════════════════════════════════════════════════════════════

/**
 * 无 seq 事件白名单
 *
 * 这些事件类型由服务器级逻辑直接发射（不经引擎信封加盖 seq），
 * 传输层将其列入旁路通道：不进水位判断、跳过去重逻辑、不触发 gap 检测。
 *
 * 白名单成员（§2.3-a）：
 *   - project_created   —— 服务器级创建事件
 *   - pong              —— 心跳回显
 *   - replay_complete   —— 握手回放完成（非引擎信封）
 *   - resync_required   —— ring 淘汰告警
 *   - project_directory_required / restored —— 系统级目录恢复弹窗
 *   - token_usage_res   —— Token 统计响应（旁路请求，无 seq，见 tokenUsage store）
 *   - error（服务器级） —— 无 project_id/seq
 */
export const NO_SEQ_EVENT_TYPES = new Set([
  'project_created',
  'pong',
  'replay_complete',
  'resync_required',
  'project_directory_required',
  'project_directory_restored',
  'project_delete_progress',
  'token_usage_res',
]);

/**
 * 判断事件是否属于无 seq 白名单
 * 注意：服务器级 error 没有独立的 type 区分——通过 seq 字段缺失在传输层旁路
 */
export function isNoSeqEvent(type: string): boolean {
  return NO_SEQ_EVENT_TYPES.has(type);
}

// ═══════════════════════════════════════════════════════════════
// 七、瞬时事件 vs 结构事件白名单
// ═══════════════════════════════════════════════════════════════

/** 瞬时事件类型（不进入 UI event log / snapshot） */
export const TRANSIENT_EVENT_TYPES = new Set([
  'agent_active',
  'agent_idle',
  'stream_delta',
  'agent_thinking',
  'tool_gen',
  'tool_start',
  'tool_complete',
  'tool_call',
  'pong',
  'replay_complete',
  'resync_required',
  'state_snapshot',
  'project_directory_required',
  'project_directory_restored',
  'project_delete_progress',
]);

/** 结构事件类型（进入 UI event log / snapshot） */
export const STRUCTURAL_EVENT_TYPES = new Set([
  'message',
  'approval_card',
  'approval_resolved',
  'agents_created',
  'agent_removed',            // [v0.9b] 进时间线，也进快照
  'instruction_injected',
  'report_submitted',
  'error',
  'recovery_notice',
  'user_echo',
]);

// ═══════════════════════════════════════════════════════════════
// 八、审批卡归一化辅助
// ═══════════════════════════════════════════════════════════════

/**
 * 审批卡归一化：处理崩溃恢复复提路径缺失字段
 *
 * [v2#4] §2.1#8 实现：
 *   - 缺 card_id  → 回填 card.approval_id
 *   - 缺 agent_id → 回填 'coordinator'
 *   - tool 无法识别时按 card 形状推断（有 proposed→team，有 target_id→task）
 */
export interface NormalizedApprovalCard {
  card_id: string;
  agent_id: string;
  tool: 'propose_agents' | 'propose_next' | string;
}

export function normalizeApprovalCard(card: ApprovalCard): NormalizedApprovalCard {
  const cid = card.card_id ?? (
    card.card && typeof card.card === 'object' && 'approval_id' in card.card
      ? String(card.card.approval_id)
      : 'unknown'
  );
  const agentId = card.agent_id ?? 'coordinator';

  let tool: string = card.tool;
  /*
   * tool 为未知值（如崩溃恢复复提路径的 "?"）时，按 card 的**形状**推断。
   *
   * ★ [v0.9b] 派活卡和移除卡都带 target_id —— 靠**有没有 instruction** 区分。
   *   不加这一条的话，一张移除卡会被推断成 propose_next，
   *   屏幕上就弹出一张「派发任务」的卡，任务内容是空的。
   */
  const known = tool === 'propose_agents' || tool === 'propose_next'
    || tool === 'propose_remove_agent';
  if (!known) {
    const body = (card.card && typeof card.card === 'object')
      ? card.card as Record<string, unknown>
      : {};
    if ('proposed' in body) {
      tool = 'propose_agents';
    } else if ('target_id' in body && 'instruction' in body) {
      tool = 'propose_next';
    } else if ('target_id' in body) {
      tool = 'propose_remove_agent';
    }
  }

  return { card_id: cid, agent_id: agentId, tool };
}

// ═══════════════════════════════════════════════════════════════
// 九、入站/出站校验函数（运行时用）
// ═══════════════════════════════════════════════════════════════

/**
 * 校验原始 JSON 是否为合法的入站事件。
 * 校验失败 → console.error + 返回 null + 走廊计数（响亮失败，不静默）。
 */
export function validateInbound(raw: unknown): InboundEvent | null {
  const result = InboundEventSchema.safeParse(raw);
  if (!result.success) {
    console.error('[contract] 入站事件校验失败', result.error.issues, raw);
    return null;
  }
  return result.data;
}

/**
 * 校验原始 JSON 是否为合法的出站指令。
 * 校验失败 → console.error + 返回 null。
 */
export function validateOutbound(raw: unknown): OutboundCommand | null {
  const result = OutboundCommandSchema.safeParse(raw);
  if (!result.success) {
    console.error('[contract] 出站指令校验失败', result.error.issues, raw);
    return null;
  }
  return result.data;
}
