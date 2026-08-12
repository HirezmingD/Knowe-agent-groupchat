/**
 * v23.test.ts — 活动可见性 + 空消息（v0.23 / v0.23.1）
 *
 * 这三条在前端这一侧要守的是：
 *   · tool_gen/tool_complete **别再被扔掉**（问题二）——后端一直在发，契约也早定义好了，
 *     只有 state.ts 里四个 case 并排 `break`，数据在门口没人开门。
 *   · 卡插在流式气泡后面时，卡**上面**那句话要留在原地（问题三的前端一半）。
 *   · 空消息是**信号**不是**消息**：不进 items、不顶群、不当预览（问题四）。
 */

import { describe, it, expect } from 'vitest';
import {
  acknowledgeTransientFrame, applyEvent, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES,
  type Conv, type AgentItem,
} from './state';
import {
  stagePhrase,
  TOOL_ACTIVITY_SEPARATOR,
  toolPhrase,
  toolTechnicalDetail,
} from './toolPhrase';

const base = { project_id: 'p1', ts: '2026-01-01T00:00:00Z' };

function conv(): Conv {
  return { projectId: 'p1', projectName: 'p1', items: [], members: [], banner: null, draft: '' };
}
function apply(c: Conv, ev: unknown): void {
  applyEvent(c, ev as never, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
}
function paintAgent(c: Conv, index = 0): void {
  const frameId = (c.items[index] as AgentItem | undefined)?.transientFrame?.id;
  if (!frameId) throw new Error(`agent item ${index} has no transient frame`);
  expect(acknowledgeTransientFrame(c, frameId)).toBe(true);
}
/** ConvList 里 lastText 的逻辑，原样搬过来钉住 */
function previewOf(c: Conv): string {
  for (let i = c.items.length - 1; i >= 0; i--) {
    const it = c.items[i];
    if (!it) continue;
    if (it.kind === 'user' || it.kind === 'agent' || it.kind === 'system') {
      if (it.text && it.text.trim()) return it.text;
      continue;
    }
  }
  return '';
}

// ═══════════════════════════════════════════════════════════════
describe('v0.23 问题二 · 推理可见性', () => {
  it('★ tool_gen 挂到正在流的气泡上（老代码直接 break 扔了）', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'be_1', seq: 1, ...base });
    apply(c, { type: 'tool_gen', agent_id: 'be_1', tool_name: 'browser_navigate', seq: 2, ...base });

    const it = c.items[c.items.length - 1] as AgentItem;
    expect(it.streaming).toBe(true);
    expect(it.activities).toEqual([{ tool: 'browser_navigate', n: 1, pendingDetail: true }]);
    expect(it.stages).toEqual([
      { stage: 'plan', state: 'complete', n: 1 },
      { stage: 'explore', state: 'active', n: 1 },
    ]);
  });

  it('★ tool_complete **不再**擦掉活动（v0.23.1 改成叠加）', () => {
    // v0.23 只有一行，所以要擦了才能显示下一个。现在是叠加的：
    // 擦掉等于把「它刚才干了什么」从用户眼前抹走，而那正是他想看的。
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'be_1', seq: 1, ...base });
    apply(c, { type: 'tool_gen', agent_id: 'be_1', tool_name: 'terminal', seq: 2, ...base });
    apply(c, { type: 'tool_complete', agent_id: 'be_1', seq: 3, ...base });

    expect((c.items[0] as AgentItem).activities).toEqual([
      { tool: 'terminal', n: 1, pendingDetail: true },
    ]);
    const stages = (c.items[0] as AgentItem).stages ?? [];
    expect(stages[stages.length - 1]?.state).toBe('complete');
  });

  it('★ 问题五：新状态叠在上一条下面，不是覆盖', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'be_1', seq: 1, ...base });
    for (const [i, t] of ['search_project_knowledge', 'safe_search_files', 'safe_patch'].entries()) {
      apply(c, { type: 'tool_gen', agent_id: 'be_1', tool_name: t, seq: i + 2, ...base });
    }
    expect((c.items[0] as AgentItem).activities).toEqual([
      { tool: 'search_project_knowledge', n: 1, pendingDetail: true },
      { tool: 'safe_search_files', n: 1, pendingDetail: true },
      { tool: 'safe_patch', n: 1, pendingDetail: true },
    ]);
  });

  it('连续同一个工具 → 合并计数，不是刷屏', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'be_1', seq: 1, ...base });
    for (let i = 0; i < 5; i++) {
      apply(c, { type: 'tool_gen', agent_id: 'be_1', tool_name: 'browser_click', seq: i + 2, ...base });
    }
    expect((c.items[0] as AgentItem).activities).toEqual([
      { tool: 'browser_click', n: 5, pendingDetail: true },
    ]);
  });

  it('活动栈有上限 —— 四十个工具调用不能把气泡撑到整屏', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'be_1', seq: 1, ...base });
    for (let i = 0; i < 40; i++) {
      apply(c, { type: 'tool_gen', agent_id: 'be_1', tool_name: 'tool_' + i, seq: i + 2, ...base });
    }
    const acts = (c.items[0] as AgentItem).activities!;
    expect(acts.length).toBeLessThanOrEqual(6);
    expect(acts[acts.length - 1].tool).toBe('tool_39');     // 留的是最近的
  });

  it('没有流式气泡时 tool_gen 自愈建立过程气泡', () => {
    const c = conv();
    apply(c, { type: 'tool_gen', agent_id: 'be_1', tool_name: 'terminal', seq: 1, ...base });
    expect(c.items).toHaveLength(1);
    expect((c.items[0] as AgentItem).streaming).toBe(true);
    expect((c.items[0] as AgentItem).stages).toEqual([
      { stage: 'verify', state: 'active', n: 1 },
    ]);
  });

  it('十次读取折叠为一个“整合”阶段计数，不刷十行工具名', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'be_1', seq: 1, ...base });
    for (let i = 0; i < 10; i++) {
      apply(c, {
        type: 'tool_gen', agent_id: 'be_1', tool_name: 'safe_read_file', seq: i + 2, ...base,
      });
    }

    const item = c.items[0] as AgentItem;
    expect(item.stages).toEqual([
      { stage: 'plan', state: 'complete', n: 1 },
      { stage: 'integrate', state: 'active', n: 10 },
    ]);
    expect(item.activities).toEqual([
      { tool: 'safe_read_file', n: 10, pendingDetail: true },
    ]);
  });

  it('★ message 落定 → 换成 MessageBubble，活动行自然消失（「中间过程消失」）', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'be_1', seq: 1, ...base });
    apply(c, { type: 'tool_gen', agent_id: 'be_1', tool_name: 'browser_navigate', seq: 2, ...base });
    paintAgent(c);
    apply(c, { type: 'message', agent_id: 'be_1', content: '搞定了，图存在 imgs/。', seq: 3, ...base });

    const it = c.items[0] as AgentItem;
    expect(it.streaming).toBe(false);          // streaming=false → 不再渲染 StreamBubble → 活动栈消失
    expect(it.text).toBe('搞定了，图存在 imgs/。');
    expect(c.items.filter((x) => x.kind === 'agent')).toHaveLength(1);   // 不重影
  });

  it('★ 问题四：活动数据永远不进正文', () => {
    // v0.23 的病根：活动行去读 item.text，而 item.text 正是最终答案要落的字段。
    // 现在活动只认 activities，两条数据永不相交 —— 结构上做不到混。
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'be_1', seq: 1, ...base });
    for (const [i, t] of ['safe_read_file', 'terminal', 'browser_navigate'].entries()) {
      apply(c, { type: 'tool_gen', agent_id: 'be_1', tool_name: t, seq: i + 2, ...base });
    }
    paintAgent(c);
    apply(c, { type: 'message', agent_id: 'be_1', content: '写完了。', seq: 9, ...base });

    expect((c.items[0] as AgentItem).text).toBe('写完了。');   // 只有 message 的内容
  });

  it('两个成员各自的活动不串台', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'fe_1', seq: 1, ...base });
    apply(c, { type: 'agent_thinking', agent_id: 'be_1', seq: 2, ...base });
    apply(c, { type: 'tool_gen', agent_id: 'fe_1', tool_name: 'safe_patch', seq: 3, ...base });

    expect((c.items[0] as AgentItem).activities).toEqual([{ tool: 'safe_patch', n: 1 }]);
    expect((c.items[1] as AgentItem).activities).toBeUndefined();
  });
});

// ═══════════════════════════════════════════════════════════════
describe('v0.23 问题二 · 工具名翻成人话', () => {
  it('常用工具映射到八阶段主文案，不暴露函数名或路径', () => {
    expect(toolPhrase('browser_navigate')).toBe('正在梳理项目结构与相关资料');
    expect(toolPhrase('terminal')).toBe('正在验证结果是否符合要求');
    expect(toolPhrase('web_search')).toBe('正在梳理项目结构与相关资料');
    expect(toolPhrase('safe_patch')).toBe('正在实施当前任务');
    expect(toolPhrase(`safe_read_file${TOOL_ACTIVITY_SEPARATOR}src/store/state.ts`))
      .toBe('正在整合信息并理解现有内容');
  });

  it('★ 表没跟上时主界面降级到规划阶段，不泄漏未知 schema 名', () => {
    expect(toolPhrase('quantum_teleport')).toBe('正在规划下一步处理方式');
    expect(toolPhrase('quantum_teleport')).not.toContain('quantum_teleport');
  });

  it('同族前缀兜底到阶段文案（新增 browser_xxx 仍有稳定展示）', () => {
    expect(toolPhrase('browser_hover')).toBe('正在梳理项目结构与相关资料');
    expect(toolPhrase('web_crawl')).toBe('正在梳理项目结构与相关资料');
  });

  it('原子工具名与参数只出现在默认折叠的技术详情', () => {
    expect(toolTechnicalDetail(`safe_read_file${TOOL_ACTIVITY_SEPARATOR}src/store/state.ts`))
      .toBe('读取项目文件 · src/store/state.ts');
    expect(toolTechnicalDetail('quantum_teleport')).toBe('调用 quantum_teleport');
  });

  it('完成阶段不会残留“✓ 正在……”的矛盾文案', () => {
    expect(stagePhrase('integrate', 'complete', '正在整合信息并理解现有内容'))
      .toBe('已完成信息整合');
  });

  it('永远不返回空串——宁可难看，不可空白', () => {
    for (const x of ['', null, undefined, '   ']) {
      expect(toolPhrase(x as string).length).toBeGreaterThan(0);
    }
  });


});

// ═══════════════════════════════════════════════════════════════
describe('v0.23 问题三 · 卡片和它上面那句话', () => {
  it('★ 卡之前那句话留在卡上面，不会被后面的话吞掉', () => {
    // 后端改成实时流之后，「好，我派给宋陈」在卡弹出**之前**就到屏幕上了。
    // v0.7b #4 的 blockedAfter 机制本来就是为这一幕写的——以前收不到流，它是死代码。
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'coordinator', seq: 1, ...base });
    apply(c, { type: 'stream_delta', agent_id: 'coordinator', content: '好，我派给宋陈。', seq: 2, ...base });
    paintAgent(c);
    apply(c, {
      type: 'approval_card', agent_id: 'coordinator', card_id: 'c1', tool: 'propose_next',
      card: { approval_id: 'c1', target_id: 'be_1', instruction: '搜图' },
      expires_at: '2026-01-01T00:05:00Z', seq: 3, ...base,
    });

    expect((c.items[0] as AgentItem).text).toBe('好，我派给宋陈。');
    expect(c.items[1]?.kind).toBe('approval');
  });

  it('落定之后不会变成两条重复的话', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'coordinator', seq: 1, ...base });
    apply(c, { type: 'stream_delta', agent_id: 'coordinator', content: '好，我派给宋陈。', seq: 2, ...base });
    paintAgent(c);
    apply(c, { type: 'message', agent_id: 'coordinator', content: '好，我派给宋陈。', seq: 3, ...base });

    const agents = c.items.filter((x) => x.kind === 'agent') as AgentItem[];
    expect(agents).toHaveLength(1);
    expect(agents[0].text).toBe('好，我派给宋陈。');
  });
});

// ═══════════════════════════════════════════════════════════════
describe('v0.23 问题四 · 空消息是信号，不是消息', () => {
  it('空消息不进 items（这一层老代码就挡住了，钉死别退化）', () => {
    const c = conv();
    apply(c, { type: 'message', agent_id: 'coordinator', content: '', seq: 1, ...base });
    expect(c.items).toHaveLength(0);
  });

  it('★ 左栏预览跳过空条目，找到 Worker 那句真话', () => {
    // 现象：Worker 说了话、项目经理 NOTHING_TO_ADD 沉默 → 左栏却写「还没有消息」。
    const c = conv();
    apply(c, { type: 'user_echo', content: '搜张图', seq: 1, ...base });
    apply(c, { type: 'message', agent_id: 'be_1', content: '图搜到了，存在 imgs/。', seq: 2, ...base });
    apply(c, { type: 'message', agent_id: 'be_1', content: '', seq: 3, ...base });      // speak 收尾
    apply(c, { type: 'message', agent_id: 'coordinator', content: '', seq: 4, ...base }); // 项目经理沉默

    expect(previewOf(c)).toBe('图搜到了，存在 imgs/。');
  });

  it('就算空气泡真的混进了 items，预览也不该被它盖住', () => {
    // 双保险：预览这一层不依赖上游永远不出错。
    const c = conv();
    apply(c, { type: 'message', agent_id: 'be_1', content: '真话', seq: 1, ...base });
    c.items.push({ kind: 'agent', agentId: 'coordinator', text: '', streaming: false });
    expect(previewOf(c)).toBe('真话');
  });

  it('一条消息都没有 → 才是真的「还没有消息」', () => {
    expect(previewOf(conv())).toBe('');
  });
});
