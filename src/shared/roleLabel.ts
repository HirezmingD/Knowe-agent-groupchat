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
 * [v1.0.38.2] 助手化称呼表 —— 单一真源（与后端 backend/roles.py ASSISTANT_PROFILES 对齐）。
 * key = 英文角色 key（Frontend/Backend/…），与 roles.* 同体系。
 * value = PRD §2.2 用户审定的「xx助手」称呼（zh 基准）。en 界面如需英文助手名再扩展。
 */
const ASSISTANT_NAME_BY_KEY: Record<string, string> = {
  Frontend: '界面设计助手',
  Backend: '编程后端助手',
  PM: '产品设计助手',
  QA: 'bug测试助手',
  Design: '美工设计助手',
  Data: '数据分析助手',
  Database: '数据库维护助手',
  DevOps: '部署上线助手',
  Security: '漏洞审查助手',
  ML: '机器学习助手',
  Mobile: '手机端开发助手',
  Game: '游戏制作助手',
  Architecture: '架构设计助手',
  GIS: 'GIS助手',
  Media: '视频音频助手',
  SRE: '系统运维助手',
  Support: '技术答疑助手',
  Writer: '文档撰写助手',
  Finance: '财务分析助手',
  Healthcare: '医疗信息助手',
  Academic: '学术研究助手',
  Legal: '法务合规助手',
  Marketing: '营销推广助手',
  Spatial: '3D/VR/AR助手',
};

/** roleKey 归一：任意形式角色名 → 英文角色 key（Frontend/Backend/…）。查不到返回原值。 */
function roleKeyOf(role: string): string {
  if (!role) return role;
  return ZH_ROLE_TO_KEY[role] ?? EN_ROLE_TO_KEY[role] ?? role;
}

/**
 * [v1.0.38.2] 助手化角色名（PRD §2.2）：「界面设计助手」这类。
 * 有助手称呼 → 用它；没有（coordinator/未知）→ 回退 roleLabel。
 * 需要「职位人话化」的展示点（消息气泡/联系人/托盘）建议用它。
 */
export function assistantRoleLabel(role: string): string {
  if (!role) return role;
  const key = roleKeyOf(role);
  const an = ASSISTANT_NAME_BY_KEY[key];
  if (an) return an;
  return roleLabel(role);
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
