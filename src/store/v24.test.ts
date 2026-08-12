/**
 * v24.test.ts — 临时态结构守卫
 *
 * 这里**不测 CSS**（样式表的路径在这个仓库里我说了不算），测的是**元素语义**：
 *
 *   v0.23.1 我把活动行渲染成 <span>，还在注释里写「没有配套 CSS 也不难看」。
 *   错了——span 是 inline 的，几条状态首尾相连挤成一行：
 *       正在读文件正在看目录正在写文件正在跑 Python正在整理交付
 *   那句「不难看」建立在一个我没验证过的默认值上。
 *
 *   ★ 教训：**能靠元素语义拿到的东西，别押在一个我看不见的样式表上。**
 *     现在用 <div>（块级是它自带的），CSS 只负责淡入和层次——
 *     样式表没加载，结构照样是对的。
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/*
 * 用 import.meta.url，不用 __dirname：vitest 跑的是 ESM，那里没有 __dirname
 * （第一版就是这么写的，当场 ENOENT）。这个写法在 vitest / node 里都成立。
 */
const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf-8');

const streamBubbleSrc = read('../components/StreamBubble.tsx');
const approvalCardSrc = read('../components/ApprovalCard.tsx');

// ═══════════════════════════════════════════════════════════════
describe('v1.0.18.2 · 可观察阶段结构', () => {
  it('★ 技术活动行仍用块级 <div>，多条不会挤成一行', () => {
    expect(streamBubbleSrc.includes('<div className="typing-acts">')).toBe(true);
    expect(streamBubbleSrc.includes('<div className="typing-label typing-act"')).toBe(true);
  });

  it('当前阶段与历史阶段是独立块级结构', () => {
    expect(streamBubbleSrc.includes('typing-stage-current')).toBe(true);
    expect(streamBubbleSrc.includes('typing-stage-history')).toBe(true);
    expect(streamBubbleSrc.includes('typing-stage-done')).toBe(true);
  });

  it('原子工具动作放进默认折叠的“技术详情”', () => {
    expect(streamBubbleSrc.includes('<details className="typing-tech">')).toBe(true);
    expect(streamBubbleSrc.includes('<summary>技术详情</summary>')).toBe(true);
  });

  it('★ 正文仍然不逐字渲染（v0.8e 的决定，别在这一版偷偷破了）', () => {
    // Props 仍可携带增量正文作最终空 content 兜底，但组件不解构、不渲染它。
    expect(streamBubbleSrc.includes('text?: string;')).toBe(true);
    expect(streamBubbleSrc.includes('{text}')).toBe(false);
    expect(streamBubbleSrc.includes('narrationLine')).toBe(false);
  });

  it('首帧保护回执经 requestAnimationFrame 发生，不在 render 中同步收口', () => {
    expect(streamBubbleSrc.includes('globalThis.requestAnimationFrame(acknowledge)')).toBe(true);
    expect(streamBubbleSrc.includes('onFramePaint(frameId)')).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════
describe('v0.24 #3 · 卡上的指令排版', () => {
  it('★ 指令走 Markdown 渲染，不再纯文本铺出来', () => {
    expect(approvalCardSrc.includes("import Markdown from './markdown'")).toBe(true);
    expect(approvalCardSrc.includes('<Markdown text={target.instruction} />')).toBe(true);
  });

  it('老的纯文本渲染已经不在了', () => {
    expect(approvalCardSrc.includes('<div className="ap-task">{target.instruction}</div>')).toBe(false);
  });

  it('复用气泡那套 Markdown（默认不放行原始 HTML —— 指令是模型写的）', () => {
    // 自己写一个 md→HTML 转换 = 自己开一个注入口。复用已经审过的那个。
    expect(approvalCardSrc.includes('dangerouslySetInnerHTML')).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════
describe('v0.24 #4 · 我有新意见', () => {
  it('三态：idle / writing / sent', () => {
    expect(approvalCardSrc.includes("useState<'idle' | 'writing' | 'sent'>('idle')")).toBe(true);
  });

  it('★ 只在派活卡上给这个按钮', () => {
    // 建群卡能直接改名字挑目录；移除卡只有移/不移两种答案。
    // 给一个用不上的按钮，比不给更糟——它让用户以为点了会有事发生。
    expect(approvalCardSrc.includes('const isTask = !!target && !isProject && !isRemove;')).toBe(true);
    expect(approvalCardSrc.includes("{isTask && feedbackMode === 'idle' && (")).toBe(true);
  });

  it('writing 态：确认/拒绝消失，换成发送/取消', () => {
    const block = approvalCardSrc.split("feedbackMode === 'writing' ? (")[1]?.slice(0, 600) ?? '';
    expect(block.includes('发送')).toBe(true);
    expect(block.includes('取消')).toBe(true);
  });

  it('sent 态：转圈 +「主管正在调整任务指令…」', () => {
    expect(approvalCardSrc.includes('主管正在调整任务指令…')).toBe(true);
    expect(approvalCardSrc.includes('thinking-dot')).toBe(true);
  });

  it('空意见不给发（灰键，而不是发一条空话给项目经理）', () => {
    expect(approvalCardSrc.includes('disabled={!feedbackText.trim()}')).toBe(true);
  });

  it('★ 走的是现成的 sendMessage，没有新增任何协议', () => {
    // 新开一个 WebSocket 指令要同时改 envelope.ts + contract.py + server.py，
    // 而后两个我手上没有 —— v0.23 的教训：不交自己验不了的东西。
    expect(approvalCardSrc.includes('const sendMessage = useKnoweStore((s) => s.sendMessage);')).toBe(true);
    expect(approvalCardSrc.includes('sendMessage(`关于刚才那个任务，我有新意见：${text}`, projectId)')).toBe(true);
  });

  it('Esc 收起、⌘/Ctrl+Enter 发送（回车留给换行）', () => {
    expect(approvalCardSrc.includes("e.key === 'Escape'")).toBe(true);
    expect(approvalCardSrc.includes("e.key === 'Enter' && (e.metaKey || e.ctrlKey)")).toBe(true);
  });

  it('展开就聚焦，让用户少点一下', () => {
    expect(approvalCardSrc.includes("if (feedbackMode === 'writing') taRef.current?.focus();")).toBe(true);
  });
});
