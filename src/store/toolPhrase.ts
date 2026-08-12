/**
 * toolPhrase.ts — 可观察工具事件 → 面向用户的工作阶段与技术详情。
 *
 * 主状态行只显示八阶段的人话文案，绝不暴露函数名、参数路径或 URL；原子工具动作
 * 仍保留在折叠的“技术详情”里，便于排障但不抢占默认界面。
 */

import i18n from '../i18n';

/** 后端在既有 tool_name 字符串中编码详情时使用的可打印分隔符。 */
export const TOOL_ACTIVITY_SEPARATOR = '\u241f';

export const WORK_STAGES = [
  'explore',
  'integrate',
  'plan',
  'implement',
  'verify',
  'review',
  'deliver',
  'wait',
] as const;

export type WorkStage = typeof WORK_STAGES[number];
export type WorkStageState = 'active' | 'complete' | 'error' | 'cancelled' | 'waiting';

export interface ParsedToolActivity {
  name: string;
  detail: string;
}

const STAGE_LABELS: Record<WorkStage, string> = {
  explore: 'tool.phrase.30',
  integrate: 'tool.phrase.38',
  plan: 'tool.phrase.57',
  implement: 'tool.phrase.18',
  verify: 'tool.phrase.69',
  review: 'tool.phrase.19',
  deliver: 'tool.phrase.01',
  wait: 'tool.phrase.53',
};

const STAGE_ACTIVE_PHRASES: Record<WorkStage, string> = {
  explore: 'tool.phrase.48',
  integrate: 'tool.phrase.46',
  plan: 'tool.phrase.50',
  implement: 'tool.phrase.45',
  verify: 'tool.phrase.51',
  review: 'tool.phrase.44',
  deliver: 'tool.phrase.47',
  wait: 'tool.phrase.49',
};

const STAGE_COMPLETE_PHRASES: Record<WorkStage, string> = {
  explore: 'tool.phrase.26',
  integrate: 'tool.phrase.21',
  plan: 'tool.phrase.23',
  implement: 'tool.phrase.22',
  verify: 'tool.phrase.25',
  review: 'tool.phrase.20',
  deliver: 'tool.phrase.24',
  wait: 'tool.phrase.54',
};

/** 全名 → 技术详情里的人话。这里允许显示参数摘要，但不用于主状态行。 */
const TOOL_PHRASES: Record<string, string> = {
  safe_read_file: 'tool.phrase.63',
  safe_write_file: 'tool.phrase.04',
  safe_patch: 'tool.phrase.02',
  safe_list_dir: 'tool.phrase.42',
  safe_search_files: 'tool.phrase.33',
  safe_delete_file: 'tool.phrase.11',
  read_external_file: 'tool.phrase.58',
  list_external_dir: 'tool.phrase.41',
  copy_external_file: 'tool.phrase.17',
  safe_bash: 'tool.phrase.67',
  terminal: 'tool.phrase.66',
  process: 'tool.phrase.55',
  execute_code: 'tool.phrase.65',
  web_search: 'tool.phrase.32',
  web_extract: 'tool.phrase.62',
  browser_navigate: 'tool.phrase.28',
  browser_snapshot: 'tool.phrase.60',
  browser_click: 'tool.phrase.37',
  browser_type: 'tool.phrase.12',
  browser_scroll: 'tool.phrase.52',
  browser_back: 'tool.phrase.68',
  browser_press: 'tool.phrase.36',
  browser_get_images: 'tool.phrase.39',
  browser_console: 'tool.phrase.43',
  browser_evaluate: 'tool.phrase.29',
  browser_dialog: 'tool.phrase.15',
  browser_screenshot: 'tool.phrase.27',
  browser_close: 'tool.phrase.03',
  vision_analyze: 'tool.phrase.09',
  search_project_knowledge: 'tool.phrase.34',
  read_project_knowledge: 'tool.phrase.64',
  speak: 'tool.phrase.56',
  submit_report: 'tool.phrase.31',
  propose_agents: 'tool.phrase.06',
  propose_next: 'tool.phrase.05',
  propose_remove_agent: 'tool.phrase.07',
  read_report: 'tool.phrase.61',
  list_handoff_dir: 'tool.phrase.40',
  read_harness_memory: 'tool.phrase.59',
};

const TOOL_STAGE: Record<string, WorkStage> = {
  safe_list_dir: 'explore',
  safe_search_files: 'explore',
  list_external_dir: 'explore',
  web_search: 'explore',
  browser_navigate: 'explore',
  browser_get_images: 'explore',

  safe_read_file: 'integrate',
  read_external_file: 'integrate',
  web_extract: 'integrate',
  browser_snapshot: 'integrate',
  browser_scroll: 'integrate',
  browser_back: 'integrate',
  search_project_knowledge: 'integrate',
  read_project_knowledge: 'integrate',
  list_handoff_dir: 'integrate',
  read_harness_memory: 'integrate',

  propose_agents: 'plan',
  propose_next: 'plan',
  propose_remove_agent: 'plan',

  safe_write_file: 'implement',
  safe_patch: 'implement',
  safe_delete_file: 'implement',
  copy_external_file: 'implement',
  browser_click: 'implement',
  browser_type: 'implement',
  browser_press: 'implement',
  browser_evaluate: 'implement',
  browser_dialog: 'implement',

  safe_bash: 'verify',
  terminal: 'verify',
  process: 'verify',
  execute_code: 'verify',
  browser_console: 'verify',
  browser_screenshot: 'verify',
  vision_analyze: 'verify',

  read_report: 'review',
  speak: 'deliver',
  submit_report: 'deliver',
};

const TOOL_FAMILIES: ReadonlyArray<readonly [string, string, WorkStage]> = [
  ['browser_', i18n.t('tool.phrase.35'), 'explore'],
  ['safe_', i18n.t('tool.phrase.16'), 'implement'],
  ['web_', i18n.t('tool.phrase.13'), 'explore'],
  ['vision_', i18n.t('tool.phrase.10'), 'verify'],
  ['propose_', i18n.t('tool.phrase.08'), 'plan'],
];

function clampOneLine(value: string, limit: number): string {
  const oneLine = value
    .replace(/[\u0000-\u001f\u007f]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return oneLine.length > limit ? `${oneLine.slice(0, limit - 1).trimEnd()}…` : oneLine;
}

/**
 * 解析兼容的工具活动 token。
 *
 * 新后端使用 U+241F；旧审计台账曾使用全角冒号，仍在这里兼容，避免把参数详情误当
 * 成工具函数名。畸形/过长详情会被清洗和限长。
 */
export function parseToolActivity(toolName: string | undefined | null): ParsedToolActivity {
  const raw = (toolName ?? '').trim();
  if (!raw) return { name: '', detail: '' };

  let splitAt = raw.indexOf(TOOL_ACTIVITY_SEPARATOR);
  let separatorLength = TOOL_ACTIVITY_SEPARATOR.length;
  if (splitAt < 0) {
    const legacy = raw.indexOf('：');
    const possibleName = legacy >= 0 ? raw.slice(0, legacy).trim() : '';
    if (legacy > 0 && /^[A-Za-z][A-Za-z0-9_]*$/.test(possibleName)) {
      splitAt = legacy;
      separatorLength = 1;
    }
  }

  const name = (splitAt < 0 ? raw : raw.slice(0, splitAt)).trim();
  const unsafeDetail = splitAt < 0 ? '' : raw.slice(splitAt + separatorLength);
  return { name, detail: clampOneLine(unsafeDetail, 84) };
}

export function normalizeWorkStage(value: unknown): WorkStage | null {
  return typeof value === 'string' && (WORK_STAGES as readonly string[]).includes(value)
    ? value as WorkStage
    : null;
}

export function normalizeWorkStageState(value: unknown): WorkStageState | null {
  return value === 'active' || value === 'complete' || value === 'error'
    || value === 'cancelled' || value === 'waiting'
    ? value
    : null;
}

/** 只按已经发生的工具事件推导展示阶段，不读取模型思维内容。 */
export function stageForTool(toolName: string | undefined | null): WorkStage {
  const { name } = parseToolActivity(toolName);
  if (!name) return 'plan';
  const exact = TOOL_STAGE[name];
  if (exact) return exact;
  for (const [prefix, , stage] of TOOL_FAMILIES) {
    if (name.startsWith(prefix)) return stage;
  }
  return 'plan';
}

export function stageLabel(stage: WorkStage): string {
  return i18n.t(STAGE_LABELS[stage] ?? '');
}

export function stagePhrase(
  stage: WorkStage,
  state: WorkStageState = 'active',
  detail?: string,
): string {
  // 后端的 stage_detail 是阶段文案（tool_ledger 固定表），与本地 i18n 映射同源；
  // 直接用本地映射保证语言跟随界面切换（不再采用后端原文）。
  void detail;
  if (state === 'error') return i18n.t('tool.phrase.stageError', { stage: i18n.t(STAGE_LABELS[stage] ?? '') });
  if (state === 'cancelled') return i18n.t('tool.phrase.stageCancelled', { stage: i18n.t(STAGE_LABELS[stage] ?? '') });
  if (state === 'waiting') return i18n.t(STAGE_ACTIVE_PHRASES.wait);
  if (state === 'complete') return i18n.t(STAGE_COMPLETE_PHRASES[stage]);
  return i18n.t(STAGE_ACTIVE_PHRASES[stage]);
}

/**
 * 工具名 → 可用于技术详情的动作描述。
 *
 * 主阶段行不调用本函数；未知工具名只会在折叠详情中出现，保证默认界面不泄漏 schema 名。
 */
export function toolTechnicalDetail(toolName: string | undefined | null): string {
  const { name, detail } = parseToolActivity(toolName);
  if (!name) return i18n.t('tool.phrase.14');

  let phrase = name ? i18n.t(TOOL_PHRASES[name] ?? '') : '';
  if (!phrase) {
    for (const [prefix, familyPhrase] of TOOL_FAMILIES) {
      if (name.startsWith(prefix)) {
        phrase = i18n.t(familyPhrase);
        break;
      }
    }
  }
  if (!phrase) phrase = i18n.t('tool.phrase.callTool', { name: name ?? '' });
  return detail ? `${phrase} · ${detail}` : (phrase ?? '');
}

/**
 * 兼容旧调用点：返回无函数名、无参数路径的用户文案。
 * StreamBubble 的主状态改用 stagePhrase；技术详情改用 toolTechnicalDetail。
 */
export function toolPhrase(toolName: string | undefined | null): string {
  return stagePhrase(stageForTool(toolName));
}
