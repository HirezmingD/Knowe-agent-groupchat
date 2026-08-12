/**
 * chat.ts — 「这是群聊，还是私聊？」**一处判定**。（[v0.8b #10]）
 *
 * 知知不是一个项目，也不是一个群——她是**一个人**。
 * 群聊有花名册、有人数、有待确认；私聊只有你和对面那一个人。
 * 在她的窗口里显示「1 人」，就像微信在跟妈妈的对话框上写「本群 1 人」一样滑稽。
 *
 * 为什么单开一个文件，而不是在 ConvList 里写一句 `id === PLATFORM_PROJECT_ID`：
 *
 *   知知只是**第一个**私聊对象。接下来每个群里的 Agent 都该能被单独拉出来私聊
 *   （「前端，你过来一下」），那时候会有一大批 conversation 是私聊。
 *   判断散在四五个组件里，加第二种私聊的时候就得满仓库找——所以现在就把它收成一处：
 *   **谁是私聊，只有这个文件说了算。**
 *
 * 未来的私聊会话 id 约定：`dm:{projectId}:{agentId}`（前缀已经认了，见下）。
 * 到那天，UI 侧一行都不用改——把会话建出来就行。
 */

import { PLATFORM_PROJECT_ID } from './avatar';

/** 未来：群内私聊的会话 id 前缀（`dm:官网改版:fe_1`） */
export const PRIVATE_CHAT_PREFIX = 'dm:';

/** 现在：写死的私聊对象。知知是第一个。 */
export const PRIVATE_CHAT_PROJECTS: readonly string[] = [PLATFORM_PROJECT_ID];

/**
 * 这个会话是私聊吗？
 *
 * 私聊窗口里**不显示**：成员人数、花名册面板、「待确认」标签。
 * （待确认：私聊里没有「群成员在等你点头」这回事——知知的审批卡是弹在她自己窗口里的，
 *   左栏列表项上不需要再挂一个角标。）
 */
export function isPrivateChat(projectId: string | null | undefined): boolean {
  if (!projectId) return false;
  return PRIVATE_CHAT_PROJECTS.includes(projectId)
    || projectId.startsWith(PRIVATE_CHAT_PREFIX);
}

/** 群聊 = 不是私聊。写出来是为了让调用处读起来是人话。 */
export function isGroupChat(projectId: string | null | undefined): boolean {
  return !!projectId && !isPrivateChat(projectId);
}

// ═══════════════════════════════════════════════════════════════
// [v0.37] 群内 Agent 私聊：会话 id = `dm:{projectId}:{agentId}`
//
//   知知是**平台级**私聊（__platform__）；这里是**群内**私聊：把某个群里的某个
//   成员（项目经理 / Worker）单独拉出来一对一说话。会话 id 把「哪个群、群里的谁」
//   都编进去，一处解析、全局通用——谁是群内私聊、私聊的是谁，只有这个文件说了算。
//
//   ★ 铁律（见 PROMPT §四）：私聊 ≠ 隐秘。id 里带着 projectId，就是为了让后端
//     据它把三级记忆写回**所属项目**——项目经理始终知道用户和每个成员私下聊了什么。
// ═══════════════════════════════════════════════════════════════

/** agentId 里可能含 ':'（不该有，但别赌）——用它做分隔符时把 agent 段留到最后。 */
export function dmSessionId(projectId: string, agentId: string): string {
  return `${PRIVATE_CHAT_PREFIX}${projectId}:${agentId}`;
}

/** 这个会话是不是**群内** Agent 私聊（区别于知知的平台私聊）。 */
export function isAgentDm(projectId: string | null | undefined): boolean {
  return !!projectId && projectId.startsWith(PRIVATE_CHAT_PREFIX);
}

/**
 * 拆出 `dm:{projectId}:{agentId}` 的两段。不是群内私聊 id → null。
 *
 * 只在第一个 ':' 处切一刀：前缀是 `dm:`（PRIVATE_CHAT_PREFIX 自带冒号），
 * 其后到**第一个冒号**是 projectId，剩下的全算 agentId（哪怕 agentId 里再有冒号）。
 */
export function parseDmId(
  sessionId: string | null | undefined,
): { projectId: string; agentId: string } | null {
  if (!sessionId || !sessionId.startsWith(PRIVATE_CHAT_PREFIX)) return null;
  const rest = sessionId.slice(PRIVATE_CHAT_PREFIX.length);
  const sep = rest.indexOf(':');
  if (sep <= 0 || sep >= rest.length - 1) return null;
  return { projectId: rest.slice(0, sep), agentId: rest.slice(sep + 1) };
}

/** 群内私聊 → 它属于哪个群（projectId）。非群内私聊 → null。 */
export function dmGroupOf(sessionId: string | null | undefined): string | null {
  return parseDmId(sessionId)?.projectId ?? null;
}

/** 群内私聊 → 私聊的是哪个 agent。非群内私聊 → null。 */
export function dmAgentOf(sessionId: string | null | undefined): string | null {
  return parseDmId(sessionId)?.agentId ?? null;
}
