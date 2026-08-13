/**
 * v05.test.tsx — v0.5 十五项修补的回归。
 *
 * 分三块：显示修复（头像/Markdown/名字）、建群卡、布局交互。
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import ConvList from '../components/ConvList';
import ApprovalCard from '../components/ApprovalCard';
import MessageBubble from '../components/MessageBubble';
import StreamBubble from '../components/StreamBubble';
import Composer from '../components/Composer';
import { Markdown } from '../components/markdown';
import { applyWidth, CLIST_COMPACT, CLIST_DEFAULT } from '../components/ResizeHandle';
import { useKnoweStore } from '../store/store';
import { projectIdForCard } from '../store/platform';
import {
  getZinniaDisplayName, COORDINATOR_AVATAR_COUNT,
  coordinatorAvatar, faceFor,
} from '../store/avatar';
import { InboundEventSchema } from '../contract/envelope';
import type { ApprovalCardData } from '../contract/envelope';
import { installAutoLoadingImage } from '../test/image';

function resetStore(): void {
  useKnoweStore.setState({
    convs: {}, projectOrder: [], activeProjectId: null, notices: [], conn: 'live',
  } as never);
}
beforeEach(() => {
  resetStore();
  installAutoLoadingImage();
  document.documentElement.classList.remove('clist-compact');
});

// ═══════════════════════════════════════════════════════════════
// #1 知知全名
// ═══════════════════════════════════════════════════════════════

describe('#1 知知叫「知知Zinnia」', () => {
  it('左栏入口用全名', () => {
    render(<ConvList />);
    expect(screen.getByText(getZinniaDisplayName())).toBeInTheDocument();
  });

  it('faceFor 给知知的名字也是全名', () => {
    expect(faceFor('zinnia', '__platform__').name).toBe(getZinniaDisplayName());
  });
});

// ═══════════════════════════════════════════════════════════════
// #5 项目经理：一项目一张脸 + 名字带项目名
// ═══════════════════════════════════════════════════════════════

describe('#5 项目经理不再全都长一张脸', () => {
  it('★ 不同项目的项目经理，不同的头像（原来 coordinator 这个 id 到处一样，脸也就一样）', () => {
    const a = coordinatorAvatar('coordinator', 'p_aaa');
    const b = coordinatorAvatar('coordinator', 'p_bbb');
    const c = coordinatorAvatar('coordinator', 'p_ccc');

    expect(new Set([a, b, c]).size).toBeGreaterThan(1);
  });

  it('同一个项目的项目经理永远是同一张脸（确定性，不会闪）', () => {
    const first = coordinatorAvatar('coordinator', 'p_1');
    for (let i = 0; i < 20; i++) {
      expect(coordinatorAvatar('coordinator', 'p_1')).toBe(first);
    }
  });

  it('取的是 Coordinator 池（1..25），不是普通 agent 池', () => {
    for (let i = 0; i < 100; i++) {
      const url = coordinatorAvatar('coordinator', `p_${i}`);
      const m = /^\.\/avatars\/Coordinator\/Coordinator_(\d{4})\.png$/.exec(url);
      expect(m).not.toBeNull();
      const idx = Number(m![1]);
      expect(idx).toBeGreaterThanOrEqual(1);
      expect(idx).toBeLessThanOrEqual(COORDINATOR_AVATAR_COUNT);
    }
  });

  it('★ 项目经理的名字带项目名：「官网改版 · 项目经理」', () => {
    // [v0.5b #3] 分隔符从 `-` 改成 ` · `（原来气泡上会连成「官网改版-项目经理 · 项目经理」）。
    //   这是本批唯一改动的测试断言——它断言的正是 #3 要求改掉的那个旧格式，
    //   不跟着改，就只能留一个红灯交付。
    expect(faceFor('coordinator', 'p_1', '官网改版').name).toBe('官网改版 · 项目经理');
    expect(faceFor('coordinator', 'p_1').name).toBe('项目经理');   // 不知道项目名时退回「项目经理」
  });

  it('普通成员不受影响（还是从 396 张的池子里取）', () => {
    expect(faceFor('fe_1', 'p_1').avatarUrl).toMatch(/^\.\/avatars\/agent\//);
    expect(faceFor('fe_1', 'p_1').name).toBeUndefined();
  });
});

// ═══════════════════════════════════════════════════════════════
// #2 / #3 头像不再退化成文字
// ═══════════════════════════════════════════════════════════════

const face = {
  name: '小前', role: '前端', glyph: '小', pal: 'av-b',
  avatarUrl: './avatars/agent/avatar_0001.png',
};

describe('#3 流式期间头像就该是头像', () => {
  it('★ StreamBubble 在预加载完成后渲染 <img>', async () => {
    const { container } = render(<StreamBubble text="正在写…" face={face} />);
    await waitFor(() => {
      expect(container.querySelector('img')).toHaveAttribute('src', face.avatarUrl);
    });
  });

  it('MessageBubble 也是', async () => {
    const { container } = render(<MessageBubble kind="agent" text="写完了" face={face} />);
    await waitFor(() => {
      expect(container.querySelector('img')).toHaveAttribute('src', face.avatarUrl);
    });
  });

  it('图片挂了才退回文字（兜底还在）', async () => {
    const { container } = render(<StreamBubble text="x" face={face} />);
    const img = await waitFor(() => {
      const loaded = container.querySelector('img');
      expect(loaded).not.toBeNull();
      return loaded as HTMLImageElement;
    });
    fireEvent.error(img);

    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('小');
  });
});

describe('#2 审批卡里的成员也有头像', () => {
  const teamCard: ApprovalCardData = {
    status: 'pending_approval',
    expires_at: new Date(Date.now() + 300_000).toISOString(),
    approval_id: 'ap_1',
    proposed: [{ id: 'fe_1', role: '前端' }],
  };

  it('★ 提议的成员还没进花名册，预加载完成后也有脸', async () => {
    const { container } = render(
      <ApprovalCard
        cardId="ap_1" projectId="p1" tool="team" card={teamCard}
        state="pending" expiresAt={teamCard.expires_at} members={[]}
      />,
    );
    await waitFor(() => {
      expect(container.querySelector('.ap-row img'))
        .toHaveAttribute('src', expect.stringContaining('./avatars/agent/'));
    });
  });
});

// ═══════════════════════════════════════════════════════════════
// #4 Markdown
// ═══════════════════════════════════════════════════════════════

describe('#4 气泡渲染 Markdown', () => {
  it('★ 换行终于有了（原来一整坨，连 \\n 都不认）', () => {
    const { container } = render(<Markdown text={'第一行\n第二行'} />);
    expect(container.querySelectorAll('br')).toHaveLength(1);
  });

  it('**粗体** / *斜体* / `代码`', () => {
    const { container } = render(<Markdown text="这是**重点**和*侧重*还有`code`" />);
    expect(container.querySelector('strong')).toHaveTextContent('重点');
    expect(container.querySelector('em')).toHaveTextContent('侧重');
    expect(container.querySelector('code')).toHaveTextContent('code');
  });

  it('列表（有序 / 无序）', () => {
    const { container } = render(<Markdown text={'- 甲\n- 乙'} />);
    expect(container.querySelectorAll('ul li')).toHaveLength(2);

    const ol = render(<Markdown text={'1. 甲\n2. 乙'} />);
    expect(ol.container.querySelectorAll('ol li')).toHaveLength(2);
  });

  it('代码块（含语言标记）', () => {
    const { container } = render(<Markdown text={'```py\nprint(1)\n```'} />);
    const pre = container.querySelector('pre')!;
    expect(pre).toHaveTextContent('print(1)');
    expect(pre.querySelector('code')).toHaveClass('language-py');
  });

  it('标题与引用', () => {
    const { container } = render(<Markdown text={'## 小标题\n> 引一句'} />);
    expect(container.querySelector('h2')).toHaveTextContent('小标题');
    expect(container.querySelector('blockquote')).toHaveTextContent('引一句');
  });

  it('★ 未闭合的代码块不许吃掉整条消息（流式期间必然出现这种半截状态）', () => {
    const { container } = render(<Markdown text={'```js\nlet a = 1'} />);
    expect(container.querySelector('pre')).toHaveTextContent('let a = 1');
  });

  it('★ 原始 HTML 整段丢弃，绝不当 HTML 执行（模型输出是不可信文本）', () => {
    const { container } = render(<Markdown text={'<script>alert(1)</script>'} />);
    expect(container.querySelector('script')).toBeNull();
    expect(container).toHaveTextContent('');
  });

  it('用户自己打的字不做 Markdown 解释（他打了 ** 就该看见 **）', () => {
    const { container } = render(<MessageBubble kind="user" text="**不是加粗**" />);
    expect(container.querySelector('strong')).toBeNull();
    expect(container.textContent).toContain('**不是加粗**');
  });
});

// ═══════════════════════════════════════════════════════════════
// #9 建群审批卡
// ═══════════════════════════════════════════════════════════════

describe('#9 建群走审批卡（项目名可改）', () => {
  const projectCard: ApprovalCardData = {
    status: 'pending_approval',
    expires_at: new Date(Date.now() + 300_000).toISOString(),
    approval_id: 'ap_p1',
    project_name: '知知提的名字',
  } as ApprovalCardData;

  function renderCard(): void {
    render(
      <ApprovalCard
        cardId="ap_p1" projectId="__platform__" tool="create_project" card={projectCard}
        state="pending" expiresAt={projectCard.expires_at} members={[]}
      />,
    );
  }

  it('★ 这种卡过得了前端 Zod 契约（v0.4 时它会被当场拒收）', () => {
    const ev = {
      type: 'approval_card', agent_id: 'zinnia', tool: 'create_project', card_id: 'ap_p1',
      card: projectCard, project_id: '__platform__', project_name: '知知Zinnia',
      seq: 1, ts: new Date().toISOString(),
    };
    expect(InboundEventSchema.safeParse(ev).success).toBe(true);
  });

  it('渲染成「创建项目」，项目名在一个可编辑的输入框里', () => {
    renderCard();
    expect(screen.getByText('创建项目')).toBeInTheDocument();
    expect(screen.getByLabelText('项目名')).toHaveValue('知知提的名字');
  });

  it('★ 用户改了名字 → 用改后的名字建项目（不是知知提的那个）', async () => {
    // [v0.7 A0] 审批卡现在也需要目录才能确认
    window.knowe = { selectDirectory: () => Promise.resolve('/test/proj'), version: 'test', isElectron: true } as never;

    const created: [string, string][] = [];
    const approved: string[] = [];
    useKnoweStore.setState({
      createProject: (id: string, name: string) => { created.push([id, name]); },
      approve: (cardId: string) => { approved.push(cardId); },
    } as never);

    renderCard();
    const input = screen.getByLabelText('项目名');
    await userEvent.clear(input);
    await userEvent.type(input, '我要的名字');
    // 选择目录
    await userEvent.click(screen.getByText('选择目录'));
    await vi.waitFor(() => expect(screen.getByLabelText('项目目录')).toHaveValue('/test/proj'));
    await userEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(created).toEqual([[projectIdForCard('ap_p1'), '我要的名字']]);
    expect(approved).toEqual(['ap_p1']);        // 卡也落定了
  });

  it('★ 审批卡使用 canonical 项目 id，并在重试时保持稳定', () => {
    const first = projectIdForCard('ap_abc');
    expect(first).toMatch(/^project_\d{14}$/);
    expect(projectIdForCard('ap_abc')).toBe(first);
  });

  it('名字清空 → 阻止确认并提示，不能建出一个没名字的群', async () => {
    // [v0.7 A0] 也需要目录
    window.knowe = { selectDirectory: () => Promise.resolve('/test/proj'), version: 'test', isElectron: true } as never;
    const created: [string, string][] = [];
    useKnoweStore.setState({
      createProject: (id: string, name: string) => { created.push([id, name]); },
      approve: () => {},
    } as never);

    renderCard();
    await userEvent.clear(screen.getByLabelText('项目名'));
    await userEvent.click(screen.getByText('选择目录'));
    await vi.waitFor(() => expect(screen.getByLabelText('项目目录')).toHaveValue('/test/proj'));
    await userEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(created).toEqual([]);
    expect(screen.getByRole('alert')).toHaveTextContent('项目得有个名字');
  });

  it('拒绝 → 不建任何项目', async () => {
    const created: string[] = [];
    const rejected: string[] = [];
    useKnoweStore.setState({
      createProject: (id: string) => { created.push(id); },
      reject: (cardId: string) => { rejected.push(cardId); },
      approve: () => {},
    } as never);

    renderCard();
    await userEvent.click(screen.getByRole('button', { name: '拒绝' }));

    expect(created).toEqual([]);
    expect(rejected).toEqual(['ap_p1']);
  });
});

// ═══════════════════════════════════════════════════════════════
// #14 左栏可拖宽 / 紧凑模式
// ═══════════════════════════════════════════════════════════════

describe('#14 左栏宽度', () => {
  it('宽度写进 CSS 变量（不走 React state —— 每像素重渲染会拖得很黏）', () => {
    applyWidth(300);
    expect(document.documentElement.style.getPropertyValue('--clist-w')).toBe('300px');
  });

  it('★ 拖窄到阈值以下 → 钳制到可完整显示 Logo 的最小宽度', () => {
    applyWidth(CLIST_COMPACT - 20);
    expect(document.documentElement.style.getPropertyValue('--clist-w')).toBe(`${CLIST_COMPACT}px`);
    expect(document.documentElement.classList.contains('clist-compact')).toBe(false);

    applyWidth(CLIST_DEFAULT);
    expect(document.documentElement.classList.contains('clist-compact')).toBe(false);
  });

  it('宽度有上下限（拖到负数也不会把左栏拖没）', () => {
    applyWidth(-500);
    const w = parseInt(document.documentElement.style.getPropertyValue('--clist-w'), 10);
    expect(w).toBeGreaterThan(0);

    applyWidth(99999);
    const w2 = parseInt(document.documentElement.style.getPropertyValue('--clist-w'), 10);
    expect(w2).toBeLessThan(1000);
  });

  it('左栏里有一条可拖的分隔线（键盘也能调）', () => {
    render(<ConvList />);
    const sep = screen.getByRole('separator', { name: '调整会话列表宽度' });
    expect(sep).toBeInTheDocument();

    fireEvent.keyDown(sep, { key: 'ArrowRight' });
    expect(document.documentElement.style.getPropertyValue('--clist-w')).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════
// #11 / #15 输入框展开
// ═══════════════════════════════════════════════════════════════

describe('#11/#15 输入框展开', () => {
  beforeEach(() => {
    useKnoweStore.setState({ activeProjectId: 'p1', conn: 'live' } as never);
  });

  it('发送键左边有个展开按钮', () => {
    render(<Composer />);
    expect(screen.getByRole('button', { name: '展开输入框' })).toBeInTheDocument();
  });

  it('★ 点展开 → 输入区长高，按钮变成「收起」；再点收回去', async () => {
    const { container } = render(<Composer />);

    await userEvent.click(screen.getByRole('button', { name: '展开输入框' }));
    expect(container.querySelector('.composer-wrap')).toHaveClass('expanded');
    expect(screen.getByRole('button', { name: '收起输入框' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '收起输入框' }));
    expect(container.querySelector('.composer-wrap')).not.toHaveClass('expanded');
  });

  it('聊天区与输入框之间有一条可拖的分隔线（键盘也能调）', () => {
    render(<Composer />);
    const grip = screen.getByRole('separator', { name: '调整输入框高度' });

    fireEvent.keyDown(grip, { key: 'ArrowUp' });
    expect(document.documentElement.style.getPropertyValue('--composer-h')).toBeTruthy();
  });
});
