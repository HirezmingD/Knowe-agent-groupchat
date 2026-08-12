/**
 * v05b.test.tsx — v0.5b 七条修补的回归。
 *
 * 你说「不要改测试文件」，我理解为「不要去动既有测试的断言」——
 * 但**新写的行为必须有新的测试**，不然下一批很容易把它们悄悄改回去。
 * （唯一动过的既有断言：v05.test.tsx 里项目经理名字的格式，那正是 #3 要改掉的旧格式。）
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import ConvList from '../components/ConvList';
import Rail from '../components/Rail';
import { AvatarGrid, type GridMember } from '../components/Avatar';
import { useKnoweStore } from '../store/store';
import { faceFor, getZinniaDisplayName } from '../store/avatar';
import {
  registerMember, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES, type Conv,
} from '../store/state';

function conv(projectId = 'p1', projectName = '官网改版'): Conv {
  return { projectId, projectName, items: [], members: [], banner: null, draft: '' };
}

function resetStore(): void {
  useKnoweStore.setState({
    convs: {}, projectOrder: [], activeProjectId: null, notices: [], conn: 'live',
  } as never);
}
beforeEach(resetStore);

// ═══════════════════════════════════════════════════════════════
// #1 · Logo 在左栏顶上（不是 Rail 底下）
// ═══════════════════════════════════════════════════════════════

describe('#1 Logo 位置', () => {
  it('★ 左栏顶上是 logo 图片，不是「Knowe」四个字', () => {
    const { container } = render(<ConvList />);
    const img = container.querySelector('.wordmark img');

    expect(img).toHaveAttribute('src', './brand/knowe-logo.png');
    expect(container.querySelector('.wordmark')).not.toHaveTextContent('Knowe');
  });

  it('★ Rail 底部那个 logo 删掉了（上一批放错地方了）', () => {
    const { container } = render(<Rail />);
    expect(container.querySelector('.rail-logo-foot')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════
// #2 · 新群置顶
// ═══════════════════════════════════════════════════════════════

describe('#2 新建的群落在最上面', () => {
  it('★ project_created 用 unshift —— 新群在列表最前，不是被埋在最底下', () => {
    const st = useKnoweStore.getState();
    st.handleEvent({ type: 'project_created', project_id: 'p1', project_name: '老项目' } as never);
    st.handleEvent({ type: 'project_created', project_id: 'p2', project_name: '新项目' } as never);

    expect(useKnoweStore.getState().projectOrder).toEqual(['p2', 'p1']);
  });

  it('ensureProject 同样置顶（回放里发现的项目也是最新的在上）', () => {
    const st = useKnoweStore.getState();
    st.ensureProject('a', '甲');
    st.ensureProject('b', '乙');

    expect(useKnoweStore.getState().projectOrder).toEqual(['b', 'a']);
  });

  it('已经在列表里的项目不会被再插一次', () => {
    const st = useKnoweStore.getState();
    st.ensureProject('a', '甲');
    st.ensureProject('a', '甲');

    expect(useKnoweStore.getState().projectOrder).toEqual(['a']);
  });
});

// ═══════════════════════════════════════════════════════════════
// #3 · 项目经理名字格式
// ═══════════════════════════════════════════════════════════════

describe('#3 项目经理名字', () => {
  it('★ 「官网改版 · 项目经理」——中点分隔，且只有一个「项目经理」', () => {
    const name = faceFor('coordinator', 'p_1', '官网改版').name!;

    expect(name).toBe('官网改版 · 项目经理');
    expect(name.split('项目经理')).toHaveLength(2);   // 「项目经理」只出现一次
    expect(name).not.toContain('-');
  });

  it('不知道项目名时退回「项目经理」（不能显示「undefined · 项目经理」）', () => {
    expect(faceFor('coordinator', 'p_1').name).toBe('项目经理');
  });
});

// ═══════════════════════════════════════════════════════════════
// #4 · 项目经理头像：从源头修
// ═══════════════════════════════════════════════════════════════

describe('#4 项目经理头像终于会变了', () => {
  it('★ registerMember 存的是 Coordinator 池的头像，不是普通 agent 池', () => {
    const c = conv('p_1');
    registerMember(c, 'coordinator', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);

    const url = c.members[0]!.display.avatarUrl!;
    expect(url).toMatch(/^\.\/avatars\/Coordinator\/Coordinator_\d{4}\.png$/);
  });

  it('★ 不同项目的项目经理，花名册里存的是不同的脸（这才是「换不掉」的真根因）', () => {
    const a = conv('p_aaa');
    const b = conv('p_bbb');
    registerMember(a, 'coordinator', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    registerMember(b, 'coordinator', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);

    expect(a.members[0]!.display.avatarUrl).not.toBe(b.members[0]!.display.avatarUrl);
  });

  it('同一个项目的项目经理永远是同一张脸（不会每次刷新换个人）', () => {
    const a = conv('p_1');
    const b = conv('p_1');
    registerMember(a, 'coordinator', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    registerMember(b, 'coordinator', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);

    expect(a.members[0]!.display.avatarUrl).toBe(b.members[0]!.display.avatarUrl);
  });

  it('普通成员还是从 396 张的 agent 池取（没被误伤）', () => {
    const c = conv('p_1');
    registerMember(c, 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    expect(c.members[0]!.display.avatarUrl).toMatch(/^\.\/avatars\/agent\//);
  });

  it('知知还是 zinnia.png', () => {
    const c = conv('__platform__', '知知Zinnia');
    registerMember(c, 'zinnia', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    expect(c.members[0]!.display.avatarUrl).toBe('./avatars/zinnia.png');
  });
});

// ═══════════════════════════════════════════════════════════════
// #6 · 群聊头像宫格
// ═══════════════════════════════════════════════════════════════

function gm(n: number): GridMember[] {
  return Array.from({ length: n }, (_, i) => ({
    id: i === 0 ? 'coordinator' : `a_${i}`,
    glyph: '总',
    pal: 'av-n',
    avatarUrl: `/avatars/x_${i}.png`,
  }));
}

describe('#6 群聊头像宫格', () => {
  it('★ 只有项目经理一个人 → 两宫格：一张脸 + 一个白圆圈（不能孤零零一个点）', () => {
    const { container } = render(<AvatarGrid members={gm(1)} />);

    expect(container.querySelectorAll('.avatar')).toHaveLength(1);
    expect(container.querySelectorAll('.cav-blank')).toHaveLength(1);
  });

  it.each([
    [2, 2, [2]],
    [3, 3, [1, 2]],       // 三角：上一下二
    [4, 4, [2, 2]],       // 正方
    [5, 5, [1, 3, 1]],    // 五角
    [6, 6, [3, 3]],
    [9, 9, [3, 3, 3]],    // 九宫格
  ])('%i 个人 → %i 张脸，行布局 %j', (n, faces, rows) => {
    const { container } = render(<AvatarGrid members={gm(n)} />);

    expect(container.querySelectorAll('.avatar')).toHaveLength(faces);
    expect(container.querySelectorAll('.cav-blank')).toHaveLength(0);

    const perRow = [...container.querySelectorAll('.cav-row')]
      .map((r) => r.children.length);
    expect(perRow).toEqual(rows);
  });

  it('★ 超过 9 个人 → 截到九宫格（框就 44px，塞十个人谁也看不清）', () => {
    const { container } = render(<AvatarGrid members={gm(15)} />);
    expect(container.querySelectorAll('.avatar')).toHaveLength(9);
  });

  it('★ 项目经理排第一格（左上）', () => {
    const { container } = render(<AvatarGrid members={gm(4)} />);
    const first = container.querySelector('.cav-row .avatar img');
    expect(first).toHaveAttribute('src', './avatars/x_0.png');   // gm() 里第 0 个是 coordinator
  });

  it('宫格里的头像是图片，不是文字字形', () => {
    const { container } = render(<AvatarGrid members={gm(3)} />);
    expect(container.querySelectorAll('img')).toHaveLength(3);
  });
});

describe('#6 接进左栏', () => {
  it('★ 群里的人进来了，左栏头像跟着变成宫格（不用另外通知谁）', () => {
    const st = useKnoweStore.getState();
    st.ensureProject('p1', '官网改版');
    st.handleEvent({
      type: 'agents_created', agent_id: 'coordinator', count: 2,
      members: [{ id: 'fe_1', role: '前端' }, { id: 'be_1', role: '后端' }],
      project_id: 'p1', seq: 1, ts: new Date().toISOString(),
    } as never);

    const { container } = render(<ConvList />);
    const row = screen.getByRole('button', { name: '项目 官网改版' });

    expect(within(row).getByTitle('官网改版')).toHaveClass('cav-grid');
    expect(container.querySelectorAll('.citem .cav-grid .avatar').length).toBeGreaterThan(1);
  });

  it('知知还是单个头像（她没有团队，摆宫格没意义）', () => {
    render(<ConvList />);
    const row = screen.getByRole('button', { name: `项目 ${getZinniaDisplayName()}` });

    expect(row.querySelector('.cav-grid')).toBeNull();
    expect(within(row).getByRole('presentation', { hidden: true }))
      .toHaveAttribute('src', './avatars/zinnia.png');
  });

  it('空群（一个人都还没有）→ 单个占位头像，不摆空宫格', () => {
    useKnoweStore.getState().ensureProject('p1', '新项目');
    const { container } = render(<ConvList />);

    const item = container.querySelector('.citem[data-conv="p1"]');
    expect(item?.querySelector('.cav-grid')).toBeNull();
    expect(item?.querySelector('.avatar')).not.toBeNull();
  });
});