/**
 * avatar.ts — 头像分配。
 *
 * ⚠ [v0.7 #4] 从「agentId 哈希」改成「洗牌池 + 一次分配、终身绑定」。
 *
 *   哈希（v0.4~v0.6）解决了「同一个人在不同地方长着不同的脸」，但没解决**撞脸**：
 *   哈希是均匀撒点，不是不重复取样。396 张池子里随机撒 8 个点，撞一次的概率
 *   接近 8%（生日问题）；项目经理池只有 25 张，一个项目一个项目经理，撞脸几乎是必然。
 *
 *   现在改成发牌：开局把整副牌洗一遍（Fisher-Yates），来一个人发一张，
 *   **发过的不再发**，一副发完了重新洗。小样本下零重复。
 *
 *   ★ 但「同一个 id 永远同一张脸」这条铁律一个字都不能松——
 *     faceFor() 在每次渲染时都会被调用（气泡、花名册、审批卡、左栏宫格）。
 *     要是每次调用都从池里抽新的一张，头像会**闪**，而且审批卡里提议的 fe_1
 *     和确认之后花名册里的 fe_1 会长得不一样。
 *     所以发牌只发一次：分配结果记在 `assigned` 里，之后每次都查表。
 *
 *   幂等的边界：
 *     · 普通成员 → 键是 `agent:<agentId>`
 *     · 项目经理     → 键是 `coordinator:<projectId>`（每个项目一位项目经理，不是同一个人打十份工）
 */

import i18n from '../i18n';

/** 知知（Zinnia）的 agent_id */
export const ZINNIA_AGENT_ID = 'zinnia';

/** [v0.5] 知知的显示名——界面上一律用全名「知知Zinnia」 */
export function getZinniaDisplayName(): string {
  return i18n.t('common.19');
}

/** 知知住的平台会话 id（后端 agents/zinnia.py 里的 PLATFORM_PROJECT_ID） */
export const PLATFORM_PROJECT_ID = '__platform__';

/** 知知的头像（固定一张，不进池子） */
export const ZINNIA_AVATAR = './avatars/zinnia.png';

/** agent 头像池总数 */
export const AGENT_AVATAR_COUNT = 396;

/** 项目经理头像池总数 */
export const COORDINATOR_AVATAR_COUNT = 25;

// ═══════════════════════════════════════════════════════════════
// [v0.8e #7] 发牌：**项目 + agent 一起做种**
//
// 老账（v0.7 #4）：一副 396 张的牌洗匀，来一个 agent 发一张，键是 `agent:<agentId>`。
// 两个毛病，用久了都露出来了：
//
//   ① **跨项目撞脸。** 键里没有项目。每个项目的前端都叫 fe_1 ——
//      于是所有项目的前端长着同一张脸。用户建了三个群，三个前端一模一样，
//      他会以为是同一个人。（这就是这次报的 bug。）
//
//   ② **每次重启换脸。** 洗牌用的是 Math.random()，进程一重启就重洗一副。
//      昨天那个前端今天变成另一个人了 —— 只是没人报，因为大家以为它本来就该这样。
//
// 新账：`hash(projectId + '/' + agentId)` 做种子取下标。
//   · 同项目同 agent → 永远同一张（跨重启也一样）
//   · 不同项目的 fe_1 → 种子不同 → 脸不同
//   · 同一项目内撞了 → **向后线性探测**，直到找到这个项目里还没人用的那张
//     （396 张池子里塞 8 个人，撞一次的概率约 8%——生日问题，不能不管）
//
// 「纯随机」这条我没选：随机 = 每次重启换一张脸。用户认的是脸，不是 id。
// ═══════════════════════════════════════════════════════════════

/**
 * [保留] 洗牌分配器。v0.8e 起主路径不再用它（改成种子哈希 + 探测），
 * 但它是导出的、测试可能还在引用 —— 留着，删掉只是给下一个人添麻烦。
 */
export class AvatarAllocator {
  private pool: number[];
  private cursor = 0;

  constructor(total: number) {
    this.pool = Array.from({ length: total }, (_, i) => i + 1);
    this.shuffle();
  }

  private shuffle(): void {
    // Fisher-Yates
    for (let i = this.pool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [this.pool[i], this.pool[j]] = [this.pool[j] as number, this.pool[i] as number];
    }
  }

  next(): number {
    if (this.cursor >= this.pool.length) {
      this.shuffle();
      this.cursor = 0;
    }
    return this.pool[this.cursor++] as number;
  }

  reset(): void {
    this.cursor = 0;
    this.shuffle();
  }
}

/** FNV-1a 32 位。要的不是密码学强度，是「散得开、跨平台一致、跑得快」。 */
function hash32(str: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** 身份键（项目 + agent） → 已经发出去的那张脸 */
const assigned = new Map<string, string>();
/** 每个项目已经用掉的下标 —— 同一个群里不许有两张一样的脸 */
const takenByProject = new Map<string, Set<number>>();

function pad(n: number): string {
  return String(n).padStart(4, '0');
}

/**
 * 定下标：种子哈希 → 撞了就往后挪一格。
 *
 * ★ 探测是必须的。种子哈希只保证「散得开」，不保证「不撞」——
 *   396 张池子里发 8 张，撞一次的概率约 8%（生日问题）。
 *   一个群里两个人顶着同一张脸，用户会当场懵。
 */
function seatFor(projectId: string, agentId: string, total: number): number {
  const key = projectId || '_';
  let taken = takenByProject.get(key);
  if (!taken) {
    taken = new Set<number>();
    takenByProject.set(key, taken);
  }

  let idx = (hash32(`${key}/${agentId}`) % total) + 1;   // 1..total
  if (taken.size < total) {
    let guard = 0;
    while (taken.has(idx) && guard++ < total) {
      idx = (idx % total) + 1;                           // 往后挪一格（环形）
    }
  }
  taken.add(idx);
  return idx;
}

/** 查表；没有就按种子发一张，记账，返回。 */
function takeOnce(
  projectId: string,
  agentId: string,
  total: number,
  path: (idx: number) => string,
): string {
  const key = `${projectId || '_'}::${agentId}`;
  const had = assigned.get(key);
  if (had) return had;

  const url = path(seatFor(projectId, agentId, total));
  assigned.set(key, url);
  return url;
}

/** 只给测试用：清空分配记录 */
export function resetAvatarAllocation(): void {
  assigned.clear();
  takenByProject.clear();
}

// ═══════════════════════════════════════════════════════════════
// 对外
// ═══════════════════════════════════════════════════════════════

export function isCoordinator(agentId: string): boolean {
  return agentId.toLowerCase() === 'coordinator';
}

/** 判断一个 agent_id 是不是知知 */
export function isZinnia(agentId: string): boolean {
  const lower = agentId.toLowerCase();
  return lower === ZINNIA_AGENT_ID
    || lower.includes('zinnia')
    || lower.includes('platform');
}

/**
 * 给一个成员发一张脸。
 *
 * 同一个项目里的同一个 agent → 永远同一张（跨重启也是）。
 * 不同项目里的同名 agent（各家的 fe_1）→ 各是各的脸。
 */
export function agentAvatar(agentId: string, projectId = ''): string {
  return takeOnce(
    projectId, `agent:${agentId}`, AGENT_AVATAR_COUNT,
    (idx) => `./avatars/agent/avatar_${pad(idx)}.png`,
  );
}

/**
 * 给一个项目的项目经理发一张脸。
 *
 * 种子里只有 projectId —— 所有项目的项目经理 agentId 都叫 `coordinator`，
 * 真正区分「这是哪一位项目经理」的是项目。一个项目只有一位项目经理，所以不会自己撞自己。
 */
export function coordinatorAvatar(_agentId: string, projectId: string): string {
  return takeOnce(
    `coordinator:${projectId || '_'}`, 'coordinator', COORDINATOR_AVATAR_COUNT,
    (idx) => `./avatars/Coordinator/Coordinator_${pad(idx)}.png`,
  );
}

/**
 * [v0.5] 屏幕上这个人叫什么、长什么脸——**一处判定，全局通用**。
 *
 * 气泡 / 流式 / 花名册 / 审批卡 / 左栏宫格全都问它。
 */
export function faceFor(agentId: string, projectId: string, projectName?: string): {
  avatarUrl: string;
  name?: string;
} {
  if (isZinnia(agentId)) {
    return { avatarUrl: ZINNIA_AVATAR, name: getZinniaDisplayName() };
  }
  if (isCoordinator(agentId)) {
    return {
      avatarUrl: coordinatorAvatar(agentId, projectId),
      // [v0.5b #3] 项目经理的名字：「官网改版 · 项目经理」。
      //   v0.7 #3：气泡里的角色副标题会检查「名字里是不是已经有这个角色」，
      //   有就不再拼一次——所以这里不会再出现「官网改版 · 项目经理 · 项目经理」。
      name: projectName ? `${projectName} · ${i18n.t('common.06')}` : i18n.t('common.06'),
    };
  }
  return { avatarUrl: agentAvatar(agentId, projectId) };
}

/**
 * 根据 agent_id 分配头像 URL。知知固定，其余按种子发一张。
 *
 * [v0.8e #7] ★ 多了一个 projectId —— **调用方一定要传**。
 *   不传的话，审批卡上那位「还没入驻的 fe_1」用的是空项目的种子，
 *   而他一旦进了群，花名册走的是 faceFor(id, 项目) —— 两张脸对不上。
 *   （v0.5 修过一次的老伤：卡上一张脸、进来另一张脸。）
 */
export function pickAvatar(agentId: string, projectId = ''): string | undefined {
  return isZinnia(agentId) ? ZINNIA_AVATAR : agentAvatar(agentId, projectId);
}
