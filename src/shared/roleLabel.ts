/**
 * roleLabel.ts — [术语替换工程] 角色英文名 → 当前语言显示名
 *
 * 后端成员数据里的 display.role 是创建时的语言快照（模块级 msg() 固化/落盘），
 * 切语言不会刷新。显示层统一经这里翻译：
 *   · 英文 key（Coordinator/Frontend/…）→ roles.* 表按当前语言翻译（en: Leader / zh: 项目经理）
 *   · 中文快照（项目经理/前端/…）→ 先经 zh.json 反查回 key，再按当前语言翻译
 *   · 英文快照（Leader/Frontend/…，英文模式下后端下发的是翻译值不是 key）
 *     → 先经 en.json 反查回 key，再按当前语言翻译
 *   · 未知角色 → 兜底返回原文，绝不显示「未知」
 */
import i18n from '../i18n';
import zh from '../locales/zh.json';
import en from '../locales/en.json';

/** zh.json 的 roles.* 表反查：中文角色名 → role key（项目经理 → Coordinator）。 */
const ZH_ROLE_TO_KEY: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const [k, v] of Object.entries(zh)) {
    if (k.startsWith('roles.') && typeof v === 'string') {
      map[v] = k.slice('roles.'.length);
    }
  }
  return map;
})();

/** en.json 的 roles.* 表反查：英文角色名 → role key（Leader → Coordinator）。 */
const EN_ROLE_TO_KEY: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const [k, v] of Object.entries(en)) {
    if (k.startsWith('roles.') && typeof v === 'string') {
      map[v] = k.slice('roles.'.length);
    }
  }
  return map;
})();

export function roleLabel(role: string): string {
  if (!role) return role;
  const key = ZH_ROLE_TO_KEY[role] ?? EN_ROLE_TO_KEY[role] ?? role;
  return i18n.t(`roles.${key}`, { defaultValue: role });
}

/**
 * memberNameLabel — 成员显示名。
 *
 *   · coordinator 的名字本质是角色名（「项目经理」/「Leader」），后端下发的是
 *     创建时的语言快照，切语言不刷新 → 经 roleLabel 反查按当前语言翻译。
 *   · 普通成员：名字是身份（出生时定的），**绝不随语言切换翻译**——
 *     中文模式掷出「陆可」就是陆可，英文模式也显示陆可；「Peak」同理。
 * 统一经这里出口，避免各组件各写一套特判。
 */
export function memberNameLabel(id: string, name: string): string {
  if (id === 'coordinator') return roleLabel(name);
  return name;
}
