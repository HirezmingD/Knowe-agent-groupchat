/**
 * tiptapMarkdown.test.ts — [v1.0.37.3] 序列化器双向单测
 *
 * 覆盖：JSON → markdown 各节点类型（含嵌套/mention）、markdown → JSON 宽松解析、
 * 往返一致性。纯函数测试，不依赖 DOM。
 */
import { describe, it, expect } from 'vitest';
import { tiptapDocToMarkdown, markdownToTiptapDoc } from './tiptapMarkdown';

const MEMBERS = [
  { id: 'writer_1', label: '小陈' },
  { id: 'ux_1', label: 'Arbor' },
];

describe('tiptapDocToMarkdown 序列化', () => {
  it('纯文本段落', () => {
    const doc = { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: '你好世界' }] }] };
    expect(tiptapDocToMarkdown(doc)).toBe('你好世界');
  });

  it('标题 1/2/3 级', () => {
    const doc = { type: 'doc', content: [
      { type: 'heading', attrs: { level: 1 }, content: [{ type: 'text', text: '一级' }] },
      { type: 'heading', attrs: { level: 3 }, content: [{ type: 'text', text: '三级' }] },
    ] };
    expect(tiptapDocToMarkdown(doc)).toBe('# 一级\n\n### 三级');
  });

  it('加粗与斜体', () => {
    const doc = { type: 'doc', content: [
      { type: 'paragraph', content: [
        { type: 'text', text: '加粗', marks: [{ type: 'bold' }] },
        { type: 'text', text: ' 普通 ' },
        { type: 'text', text: '斜体', marks: [{ type: 'italic' }] },
      ] },
    ] };
    expect(tiptapDocToMarkdown(doc)).toBe('**加粗** 普通 *斜体*');
  });

  it('无序/有序列表', () => {
    const doc = { type: 'doc', content: [
      { type: 'bulletList', content: [
        { type: 'listItem', content: [{ type: 'text', text: '甲' }] },
        { type: 'listItem', content: [{ type: 'text', text: '乙' }] },
      ] },
      { type: 'orderedList', content: [
        { type: 'listItem', content: [{ type: 'text', text: '一' }] },
        { type: 'listItem', content: [{ type: 'text', text: '二' }] },
      ] },
    ] };
    expect(tiptapDocToMarkdown(doc)).toBe('- 甲\n- 乙\n\n1. 一\n2. 二');
  });

  it('代码块（含语言标注）', () => {
    const doc = { type: 'doc', content: [
      { type: 'codeBlock', attrs: { language: 'python' }, content: [{ type: 'text', text: 'print(1)' }] },
    ] };
    expect(tiptapDocToMarkdown(doc)).toBe('```python\nprint(1)\n```');
  });

  it('mention 节点 → @名字（无尾随空格）', () => {
    const doc = { type: 'doc', content: [
      { type: 'paragraph', content: [
        { type: 'text', text: '请 ' },
        { type: 'mention', attrs: { id: 'ux_1', label: 'Arbor' } },
        { type: 'text', text: ' 处理' },
      ] },
    ] };
    expect(tiptapDocToMarkdown(doc)).toBe('请 @Arbor 处理');
  });

  it('空文档 → 空串', () => {
    expect(tiptapDocToMarkdown({ type: 'doc', content: [] })).toBe('');
  });

  it('未知节点降级为文本（不丢字）', () => {
    const doc = { type: 'doc', content: [
      { type: 'paragraph', content: [{ type: 'unknownNode', content: [{ type: 'text', text: '残片' }] }] },
    ] };
    expect(tiptapDocToMarkdown(doc)).toBe('残片');
  });
});

describe('markdownToTiptapDoc 反序列化（宽松）', () => {
  it('纯文本 → 段落', () => {
    const doc = markdownToTiptapDoc('你好世界', MEMBERS);
    expect(doc.type).toBe('doc');
    expect(doc.content).toHaveLength(1);
    expect(doc.content![0].type).toBe('paragraph');
  });

  it('# 标题', () => {
    const doc = markdownToTiptapDoc('## 小节', MEMBERS);
    expect(doc.content![0]).toMatchObject({ type: 'heading', attrs: { level: 2 } });
  });

  it('- 列表与 1. 列表', () => {
    const doc = markdownToTiptapDoc('- 甲\n- 乙\n\n1. 一\n2. 二', MEMBERS);
    expect(doc.content![0].type).toBe('bulletList');
    expect(doc.content![0].content).toHaveLength(2);
    expect(doc.content![1].type).toBe('orderedList');
  });

  it('代码块围栏', () => {
    const doc = markdownToTiptapDoc('```js\nconst a = 1;\n```', MEMBERS);
    expect(doc.content![0].type).toBe('codeBlock');
    expect(doc.content![0].attrs).toMatchObject({ language: 'js' });
  });

  it('内联加粗/斜体', () => {
    const doc = markdownToTiptapDoc('**加粗** 和 *斜体*', MEMBERS);
    const p = doc.content![0];
    expect(p.content![0]).toMatchObject({ type: 'text', text: '加粗', marks: [{ type: 'bold' }] });
    expect(p.content![2]).toMatchObject({ type: 'text', text: '斜体', marks: [{ type: 'italic' }] });
  });

  it('@名字 → mention 节点（匹配成员）', () => {
    const doc = markdownToTiptapDoc('@Arbor 干活', MEMBERS);
    const p = doc.content![0];
    expect(p.content![0]).toMatchObject({ type: 'mention', attrs: { id: 'ux_1', label: 'Arbor' } });
  });

  it('@未知名字 → 保持文本', () => {
    const doc = markdownToTiptapDoc('@路人甲 干活', MEMBERS);
    const p = doc.content![0];
    expect(p.content![0]).toMatchObject({ type: 'text', text: '@路人甲' });
  });
});

describe('往返一致性', () => {
  it('序列化 → 反序列化 → 再序列化 稳定', () => {
    const md = '# 标题\n\n正文带 **加粗** 和 *斜体*\n\n- 甲\n- 乙\n\n1. 一\n2. 二\n\n```py\nx = 1\n```\n\n请 @Arbor 处理';
    const doc = markdownToTiptapDoc(md, MEMBERS);
    const round = tiptapDocToMarkdown(doc);
    // 往返后关键内容不丢（宽松解析允许细微格式差异，但语义必须完整）。
    expect(round).toContain('# 标题');
    expect(round).toContain('**加粗**');
    expect(round).toContain('*斜体*');
    expect(round).toContain('- 甲');
    expect(round).toContain('1. 一');
    expect(round).toContain('```py');
    expect(round).toContain('@Arbor');
  });
});
