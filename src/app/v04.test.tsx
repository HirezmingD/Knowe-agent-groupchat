/**
 * v04.test.tsx — v0.4 新增行为的回归。
 *
 * 三块：
 *   一、知知（左栏固定入口 / 不是项目 / 没有花名册 / 建完项目自动切过去）
 *   二、头像（确定性派生 / 图片渲染 / 加载失败退回字形）
 *   三、姓名中英各半
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import ConvList from '../components/ConvList';
import RosterPanel from '../components/RosterPanel';
import { Avatar } from '../components/Avatar';
import { useKnoweStore } from '../store/store';
import { selectProjectList, selectIsPlatform } from '../store/selectors';
import {
  PLATFORM_PROJECT_ID, ZINNIA_AVATAR, getZinniaDisplayName, AGENT_AVATAR_COUNT,
  agentAvatar, isZinnia, pickAvatar,
} from '../store/avatar';
import {
  registerMember, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES, type Conv,
} from '../store/state';
import { installAutoLoadingImage } from '../test/image';

function conv(): Conv {
  return { projectId: 'p1', items: [], members: [], banner: null, draft: '', unread: 0 };
}

/** 把 store 恢复到刚打开软件的样子 */
function resetStore(): void {
  useKnoweStore.setState({
    convs: {}, projectOrder: [], activeProjectId: null, notices: [], conn: 'live',
  } as never);
}

beforeEach(() => {
  resetStore();
  installAutoLoadingImage();
});

// ═══════════════════════════════════════════════════════════════
// 一、知知
// ═══════════════════════════════════════════════════════════════

describe('知知 · 左栏固定入口', () => {
  it('★ 一个项目都没有时，左栏也有知知（不是一片空白）', () => {
    render(<ConvList />);
    expect(screen.getByRole('button', { name: `私聊 ${getZinniaDisplayName()}` })).toBeInTheDocument();
  });

  it('知知预加载完成后用 zinnia.png，不是字形头像', async () => {
    render(<ConvList />);
    const row = screen.getByRole('button', { name: `私聊 ${getZinniaDisplayName()}` });
    await waitFor(() => {
      expect(within(row).getByRole('presentation', { hidden: true }))
        .toHaveAttribute('src', ZINNIA_AVATAR);
    });
  });

  it('★ 知知不是项目——不出现在项目列表里（否则会变成一个能归档的假项目）', () => {
    useKnoweStore.getState().ensureProject(PLATFORM_PROJECT_ID, '知知');
    useKnoweStore.getState().ensureProject('p1', '官网改版');

    const list = selectProjectList(useKnoweStore.getState());
    expect(list.map((p) => p.id)).toEqual(['p1']);
  });

  it('全局搜索能找到知知，并暂时用结果列表替换会话列表', async () => {
    useKnoweStore.getState().ensureProject('p1', '官网改版');
    render(<ConvList />);

    await userEvent.type(screen.getByLabelText('全局搜索'), '知知');

    const results = await screen.findAllByRole('option');
    expect(results.some((row) => row.textContent?.includes(getZinniaDisplayName()))).toBe(true);
    expect(screen.queryByRole('button', { name: '项目 官网改版' })).not.toBeInTheDocument();
  });

  it('点知知 → 切到平台会话', async () => {
    render(<ConvList />);
    await userEvent.click(screen.getByRole('button', { name: `私聊 ${getZinniaDisplayName()}` }));

    expect(useKnoweStore.getState().activeProjectId).toBe(PLATFORM_PROJECT_ID);
    expect(selectIsPlatform(useKnoweStore.getState())).toBe(true);
  });
});

describe('知知 · 没有团队', () => {
  it('★ 平台会话下花名册整个不显示（知知不组队）', () => {
    useKnoweStore.getState().ensureProject(PLATFORM_PROJECT_ID, '知知');
    useKnoweStore.getState().switchProject(PLATFORM_PROJECT_ID);

    const { container } = render(<RosterPanel open onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('普通项目下花名册照常显示', () => {
    useKnoweStore.getState().ensureProject('p1', '官网改版');
    useKnoweStore.getState().switchProject('p1');

    render(<RosterPanel open onClose={() => {}} />);
    expect(screen.getByLabelText('成员花名册')).toBeInTheDocument();
  });
});

describe('知知 · 建完项目自动切过去', () => {
  it('★ 正在跟知知说话时新项目出现 → 自动切到新项目（不用自己去左栏点）', () => {
    const st = useKnoweStore.getState();
    st.ensureProject(PLATFORM_PROJECT_ID, '知知');
    st.switchProject(PLATFORM_PROJECT_ID);

    st.handleEvent({
      type: 'project_created', project_id: 'p_new', project_name: '官网改版',
    } as never);

    expect(useKnoweStore.getState().activeProjectId).toBe('p_new');
  });

  it('★ 正在看别的项目时新项目出现 → 不抢焦点（别打断用户）', () => {
    const st = useKnoweStore.getState();
    st.ensureProject('p1', '项目一');
    st.switchProject('p1');

    st.handleEvent({
      type: 'project_created', project_id: 'p2', project_name: '项目二',
    } as never);

    expect(useKnoweStore.getState().activeProjectId).toBe('p1');
  });
});

// ═══════════════════════════════════════════════════════════════
// 二、头像
// ═══════════════════════════════════════════════════════════════

describe('头像 · 确定性派生', () => {
  it('★ 同一个 id 永远同一张脸（原来是 Math.random —— 卡上一张脸、花名册另一张脸）', () => {
    const a = pickAvatar('fe_1');
    for (let i = 0; i < 50; i++) {
      expect(pickAvatar('fe_1')).toBe(a);
    }
  });

  it('不同 id 会散开（不是所有人都长一张脸）', () => {
    const faces = new Set(
      Array.from({ length: 40 }, (_, i) => agentAvatar(`fe_${i}`)),
    );
    expect(faces.size).toBeGreaterThan(20);
  });

  it('派生出的下标始终落在头像池里（1..396，四位补零）', () => {
    for (let i = 0; i < 200; i++) {
      const url = agentAvatar(`agent_${i}`);
      const m = /^\.\/avatars\/agent\/avatar_(\d{4})\.png$/.exec(url);
      expect(m).not.toBeNull();
      const idx = Number(m![1]);
      expect(idx).toBeGreaterThanOrEqual(1);
      expect(idx).toBeLessThanOrEqual(AGENT_AVATAR_COUNT);
    }
  });

  it('知知固定 zinnia.png；coordinator 从池子里取', () => {
    expect(pickAvatar('zinnia')).toBe(ZINNIA_AVATAR);
    expect(isZinnia('__platform__')).toBe(true);
    expect(pickAvatar('coordinator')).toMatch(/^\.\/avatars\/agent\//);
  });
});

describe('头像 · Avatar 组件', () => {
  it('给了 src → 预加载完成后渲染 <img>', async () => {
    const { container } = render(<Avatar glyph="前" src="./avatars/agent/avatar_0001.png" />);
    await waitFor(() => {
      expect(container.querySelector('img')).toHaveAttribute('src', './avatars/agent/avatar_0001.png');
    });
  });

  it('没给 src → 还是字形（老行为不变）', () => {
    const { container } = render(<Avatar glyph="前" />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toBe('前');
  });

  it('★ 已显示的图片加载失败 → 退回字形，绝不留一个空白圆圈', async () => {
    const { container } = render(<Avatar glyph="前" src="./avatars/agent/不存在.png" />);
    const img = await waitFor(() => {
      const loaded = container.querySelector('img');
      expect(loaded).not.toBeNull();
      return loaded as HTMLImageElement;
    });

    fireEvent.error(img);   // 走 React 合成事件，原生 dispatchEvent 到不了 onError

    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toBe('前');
  });
});

describe('头像 · 成员注册时带上头像', () => {
  it('registerMember 给每个成员一张脸', () => {
    const c = conv();
    registerMember(c, 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    expect(c.members[0]!.display.avatarUrl).toBe(pickAvatar('fe_1', c.projectId));
  });

  it('知知用 zinnia.png', () => {
    const c = conv();
    registerMember(c, 'zinnia', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    expect(c.members[0]!.display.avatarUrl).toBe(ZINNIA_AVATAR);
  });
});

// ═══════════════════════════════════════════════════════════════
// 三、姓名中英各半
// ═══════════════════════════════════════════════════════════════

describe('成员姓名 · 后端权威且确定', () => {
  it('★ 连续注册 100 个成员，重建花名册后名字仍完全一致', () => {
    const names = (): string[] => Array.from({ length: 100 }, (_, i) => {
      const c = conv();
      registerMember(c, `fe_${i}`, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
      return c.members[0]!.display.name;
    });

    expect(names()).toEqual(names());
  });

  it('字形永远跟着显示名走', () => {
    const c = conv();
    registerMember(c, 'fe_1', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
    const d = c.members[0]!.display;

    expect(d.name).toBeTruthy();
    expect(d.glyph).toBe(d.name[0]);
  });

  it('★ 平台角色不掷硬币：知知永远叫「知知」，项目经理永远叫「项目经理」', () => {
    const rand = vi.spyOn(Math, 'random').mockReturnValue(0.99);   // 逼它选英文
    try {
      const c = conv();
      registerMember(c, 'zinnia', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
      registerMember(c, 'coordinator', DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);

      expect(c.members[0]!.display.name).toBe(getZinniaDisplayName());   // [v0.5 #1] 全名
      expect(c.members[1]!.display.name).toBe('项目经理');
    } finally {
      rand.mockRestore();
    }
  });
});
