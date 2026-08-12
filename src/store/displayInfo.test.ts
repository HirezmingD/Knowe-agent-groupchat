/**
 * displayInfo.test.ts — 身份解析（agentId → 屏幕上那个人是谁）
 *
 * 这条链只要断一环，用户看到的就是 `fe_1` 这种机器 ID，不是「小前 · 前端」。
 *
 * 回退顺序（从最可信到最兜底）：
 *   1. 会话花名册里已注册的成员（display 已定，含随机人名）
 *   2. AGENTS 静态模板（coordinator / zinnia 这类固定角色）
 *   3. 按 id 前缀匹配角色模板（fe_1 → fe 模板 → 「前端」）
 *   4. 都认不出 → 用 id 本身，绝不显示 undefined
 */

import { describe, it, expect } from 'vitest';
import {
  displayInfo, registerMember,
  DEFAULT_AGENTS, DEFAULT_ROLE_TYPES,
  type Conv,
} from './state';
import { getZinniaDisplayName } from './avatar';

function conv(): Conv {
  return { projectId: 'p1', items: [], members: [], banner: null, draft: '' };
}

describe('displayInfo · 回退链', () => {
  it('① 花名册里的成员 → 用成员自己的 display（人名是注册时定的）', () => {
    const c = conv();
    registerMember(c, 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    const registered = c.members[0]!.display;

    expect(displayInfo(c, 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES)).toEqual(registered);
  });

  it('② 静态模板：coordinator → 项目经理', () => {
    const d = displayInfo(conv(), 'coordinator', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    expect(d.name).toBe('项目经理');
    expect(d.glyph).toBe('总');
  });

  it('③ 前缀匹配：fe_1 → 前端模板（角色对，名字带序号）', () => {
    const d = displayInfo(conv(), 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    expect(d.role).toBe('前端');
    expect(d.roleEn).toBe('Frontend');
    expect(d.name).toContain('前端');
  });

  it.each([
    ['be_2', '后端'],
    ['pm_1', '产品'],
    ['qa_3', '测试'],
    ['ux_1', '设计'],
  ])('③ 前缀匹配：%s → %s', (id, role) => {
    expect(displayInfo(conv(), id, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES).role).toBe(role);
  });

  it('④ 完全认不出的 id → 退回 id 本身，绝不显示 undefined', () => {
    const d = displayInfo(conv(), '天外飞仙', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    expect(d.name).toBe('天外飞仙');
    expect(d.glyph).toBe('天');
    expect(d.role).toBeTruthy();
  });

  it('④ agents/roleTypes 都是 null（配置没加载）→ 仍然不炸', () => {
    const d = displayInfo(conv(), 'fe_1', null, null);
    expect(d.name).toBe('fe_1');
    expect(d.glyph).toBe('f');
  });
});

describe('registerMember · 注册', () => {
  it('幂等：同一个 id 注册两次 → 花名册里只有一个', () => {
    const c = conv();
    registerMember(c, 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    const firstName = c.members[0]!.display.name;
    registerMember(c, 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);

    expect(c.members).toHaveLength(1);
    expect(c.members[0]!.display.name).toBe(firstName);   // 名字也不许变（不能每次刷新换个人名）
  });

  it('普通 agent 注册时会拿到一个人名（不是 fe_1 这种机器 ID）', () => {
    const c = conv();
    registerMember(c, 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    const d = c.members[0]!.display;

    expect(d.name).not.toBe('fe_1');
    expect(d.glyph).toBe(d.name.charAt(0));               // 字形 = 名字首字
    expect(d.avatarUrl).toBeTruthy();
  });

  it('★ 平台角色（coordinator / zinnia）不改名——它们是固定身份', () => {
    const c = conv();
    registerMember(c, 'coordinator', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    expect(c.members[0]!.display.name).toBe('项目经理');

    registerMember(c, 'zinnia', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    expect(c.members[1]!.display.name).toBe(getZinniaDisplayName());   // [v0.5 #1] 全名
  });

  it('★ 不污染静态模板（immer 冻结防护：display 必须是展开的新对象）', () => {
    const c = conv();
    registerMember(c, 'coordinator', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);

    expect(c.members[0]!.display).not.toBe(DEFAULT_AGENTS.coordinator);  // 不是同一个引用
    expect(DEFAULT_AGENTS.coordinator!.avatarUrl).toBeUndefined();       // 模板没被写脏
  });

  it('带 role 参数注册 → 成员初始状态跟着走', () => {
    const c = conv();
    registerMember(c, 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES, 'busy');
    expect(c.members[0]!.state).toBe('busy');
  });
});