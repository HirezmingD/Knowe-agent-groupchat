/**
 * toolActivity.ts — 原子工具详情与八阶段时间线的纯聚合逻辑。
 *
 * 工具调用仍按既有规则配对/合并，供默认折叠的技术详情使用；主界面只读取
 * WorkStageLine。所有状态均由可观察事件推导，live-only，不参与 Runtime 决策。
 */

import {
  normalizeWorkStage,
  normalizeWorkStageState,
  parseToolActivity,
  stageForTool,
  type WorkStage,
  type WorkStageState,
} from './toolPhrase';

export interface ToolActivityLine {
  tool: string;
  n: number;
  /** core 先发的纯工具名，等待执行层补发参数详情。 */
  pendingDetail?: boolean;
}

export interface WorkStageLine {
  stage: WorkStage;
  state: WorkStageState;
  /** 后端可选的人话详情；主状态渲染前还会再做脱敏。 */
  detail?: string;
  /** 同阶段连续发生的可观察动作数量，仅供辅助说明。 */
  n: number;
}

export interface ObservableStageEvent {
  stage?: unknown;
  stage_detail?: unknown;
  stage_state?: unknown;
  phase?: unknown;
  tool_name?: unknown;
}

function stageDetailOf(event: ObservableStageEvent): string | undefined {
  const value = typeof event.stage_detail === 'string' ? event.stage_detail.trim() : '';
  return value || undefined;
}

export function stageFromObservableEvent(
  event: ObservableStageEvent,
  fallback: WorkStage = 'plan',
): WorkStage {
  return normalizeWorkStage(event.stage)
    || (typeof event.tool_name === 'string' && event.tool_name ? stageForTool(event.tool_name) : null)
    || (event.phase === 'waiting' ? 'wait' : null)
    || fallback;
}

export function stageStateFromObservableEvent(
  event: ObservableStageEvent,
  fallback: WorkStageState = 'active',
): WorkStageState {
  return normalizeWorkStageState(event.stage_state) || fallback;
}

/**
 * 记录一次阶段变化：旧的 active 阶段在进入新阶段时自动闭合为 complete；同阶段事件
 * 原地累加，不制造十条“读取文件”。只保留当前阶段与最近若干已完成阶段。
 */
export function appendWorkStage(
  list: WorkStageLine[],
  stage: WorkStage,
  state: WorkStageState = 'active',
  detail?: string,
  maxLines = 6,
): WorkStageLine {
  const last = list[list.length - 1];
  if (last && last.stage === stage) {
    last.state = state;
    last.n += 1;
    if (detail) last.detail = detail;
    return last;
  }

  if (last && (last.state === 'active' || last.state === 'waiting')) last.state = 'complete';
  const line: WorkStageLine = { stage, state, detail, n: 1 };
  list.push(line);
  while (list.length > Math.max(2, maxLines)) list.shift();
  return line;
}

export function appendObservableStage(
  list: WorkStageLine[],
  event: ObservableStageEvent,
  fallback: WorkStage = 'plan',
  maxLines = 6,
): WorkStageLine {
  return appendWorkStage(
    list,
    stageFromObservableEvent(event, fallback),
    stageStateFromObservableEvent(event),
    stageDetailOf(event),
    maxLines,
  );
}

/** 为 success/error/cancel/wait 收口，不新增尚未发生的中间步骤。 */
export function finishWorkStages(
  list: WorkStageLine[],
  state: WorkStageState,
  terminalStage?: WorkStage,
  detail?: string,
  maxLines = 6,
): void {
  if (terminalStage) {
    appendWorkStage(list, terminalStage, state, detail, maxLines);
    return;
  }
  const last = list[list.length - 1];
  if (last) {
    last.state = state;
    if (detail) last.detail = detail;
  } else {
    appendWorkStage(list, state === 'waiting' ? 'wait' : 'plan', state, detail, maxLines);
  }
}

/** 把一条兼容 tool_name 事件合并进技术详情栈（原地修改）。 */
export function appendToolActivity(
  list: ToolActivityLine[],
  tool: string,
  maxLines: number,
): void {
  const parsed = parseToolActivity(tool);
  if (!parsed.name) return;

  if (parsed.detail) {
    const pendingIdx = list.findIndex((line) => (
      line.pendingDetail === true
      && parseToolActivity(line.tool).name === parsed.name
    ));

    if (pendingIdx >= 0) {
      const pending = list[pendingIdx];
      let concreteIdx = pendingIdx;
      if (pending.n > 1) {
        pending.n -= 1;
        list.splice(pendingIdx, 0, { tool, n: 1, pendingDetail: false });
      } else {
        pending.tool = tool;
        pending.pendingDetail = false;
      }

      const line = list[concreteIdx];
      const prev = list[concreteIdx - 1];
      if (prev && !prev.pendingDetail && prev.tool === line.tool) {
        prev.n += line.n;
        list.splice(concreteIdx, 1);
        concreteIdx -= 1;
      }
      const current = list[concreteIdx];
      const next = list[concreteIdx + 1];
      if (current && next && !current.pendingDetail && !next.pendingDetail
          && next.tool === current.tool) {
        current.n += next.n;
        list.splice(concreteIdx + 1, 1);
      }
    } else {
      const last = list[list.length - 1];
      if (last && !last.pendingDetail && last.tool === tool) last.n += 1;
      else list.push({ tool, n: 1, pendingDetail: false });
    }
  } else {
    const last = list[list.length - 1];
    if (last && last.pendingDetail && parseToolActivity(last.tool).name === parsed.name) {
      last.n += 1;
    } else {
      list.push({ tool, n: 1, pendingDetail: true });
    }
  }

  while (list.length > Math.max(1, maxLines)) list.shift();
}
