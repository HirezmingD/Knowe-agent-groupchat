/**
 * markdown.tsx — 气泡里的排版。（[v0.8b #8]）
 *
 * 谁在用：MessageBubble（定格的 agent 消息）、StreamBubble（还在流的）、ApprovalCard（指令框）。
 * 用户自己打的字**不走这里**——他打了 `**` 就该看见 `**`，不能替他改写（v0.5 #4 的规矩）。
 *
 * v0.8b 之前这里只认基础 Markdown：Agent 一写表格就是一堆竖线，一写公式就是一堆美元号。
 * 而项目经理做的事恰恰是「把方案列成表」「把估算写成式子」——最该被排版的两样东西，
 * 偏偏是唯二排不出来的。
 *
 * 现在：
 *   · 表格 → remark-gfm（顺带把删除线、任务列表、自动链接也带上了）
 *   · 公式 → remark-math + rehype-katex（$…$ 行内、$$…$$ 块级）
 *   · 单个换行 → remark-breaks（LLM 从不写两个空格再换行，但它换行就是想换行）
 *   · 代码高亮 → rehype-highlight（v0.8d #2）
 *
 * ⚠ 不用 rehype-raw：气泡里的内容是**模型生成的**，等于半个不可信输入。
 *   HTML 一律被剥成文本（实测 `<script>alert(1)</script>` → `alert(1)` 纯文本）。
 *
 * ─────────────────────────────────────────────────────────────────────
 * [v1.0.23.15] ★★ 渲染架构重做：ReactMarkdown 组件 → unified 管道 + HTML 缓存
 *
 * 背景（2026-08-05 CDP 实测）：切群 = 全部消息销毁重建 = 同一段文本每次挂载
 * 都全量重跑解析管道。测试1 群仅 14 条消息就阻塞主线程 2 秒+。
 *
 * 第一版缓存（ReactElement LRU）**无效**——实测缓存命中后切群仍 ~1.9 秒：
 * 缓存的 ReactElement 只是 JSX 描述符，React 渲染它时 <ReactMarkdown> 组件
 * 函数会重新执行、内部重新解析。缓存省了「创建 JSX」，没省「解析管道」。
 *
 * 正解（本版）：**缓存解析后的 HTML 字符串**。
 *   1. unified 管道（remark-parse → gfm/math/breaks → remark-rehype →
 *      highlight/katex → 行号/表格/外链 rehype 插件 → rehype-stringify）
 *      把 markdown 同步解析成 HTML 字符串
 *   2. LRU 缓存 Map<文本, HTML>：命中 = 零解析、零组件树
 *   3. 渲染 = dangerouslySetInnerHTML（一次性设置，无 React 组件开销）
 *
 * 安全边界：
 *   · 无 rehype-raw → 原始 HTML 剥离为文本（与 react-markdown 同源安全模型）
 *   · 渲染层零事件监听（链接 target/rel 是属性，非监听器）
 *   · KaTeX 输出带 class 的 span，CSS 已就位；hljs class 色板已在 CSS
 *
 * 代码块行号从 React 组件搬进 rehype 插件（rehypeLineGutter）：
 *   高亮后一行代码被切成十几个 span，DOM 里没有「行」可数——
 *   从 hast 子树递归收集文本数行数，在代码左边渲染一根号码带（.ln）。
 */

import React, { useMemo } from 'react';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import remarkBreaks from 'remark-breaks';
import remarkRehype from 'remark-rehype';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import rehypeStringify from 'rehype-stringify';
import { visit } from 'unist-util-visit';

// KaTeX 的样式表。放在这儿而不是 main.tsx：谁用谁带，删掉这个组件就一起干净。
import 'katex/dist/katex.min.css';

/* eslint-disable @typescript-eslint/no-explicit-any */
type AnyNode = any;

/**
 * `\[ … \]` / `\( … \)` → `$$ … $$` / `$ … $`
 *
 * remark-math 只认美元号，而 DeepSeek / GPT 一类模型经常吐 LaTeX 的方括号定界符。
 * 与其去改 remark 的语法，不如在门口把定界符统一了。
 *
 * ★ 代码块里的不许动。 ```python 里的 `\[` 是代码，不是公式；
 *   一刀切地替换，会把别人的代码改坏——这比不渲染公式糟糕得多。
 *   所以按 ``` 围栏切开，只在围栏之外替换。
 */
function normalizeMath(src: string): string {
  const parts = src.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g);
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part;          // 奇数段 = 围栏/行内代码，原样放行
      return part
        .replace(/\\\[([\s\S]*?)\\\]/g, (_m, body) => `$$${body}$$`)
        .replace(/\\\(([\s\S]*?)\\\)/g, (_m, body) => `$${body}$`);
    })
    .join('');
}

/** 从 hast 子树递归收集纯文本（高亮后 children 是一堆 span，拼不回原文，递归拼）。 */
function collectText(node: AnyNode): string {
  if (node.type === 'text') return String(node.value ?? '');
  if (Array.isArray(node.children)) return node.children.map(collectText).join('');
  return '';
}

/** [v0.8e #6] 代码块行号带：code 左边渲染一根 .ln 号码带 + .code-body 包裹正文。 */
function rehypeLineGutter(): (tree: AnyNode) => void {
  return (tree) => {
    visit(tree, { type: 'element', tagName: 'code' }, (node: AnyNode) => {
      const cls = String(Array.isArray(node.properties?.className)
        ? (node.properties.className as string[]).join(' ')
        : (node.properties?.className ?? ''));
      const isBlock = /language-[\w+#.-]+/.test(cls);
      const raw = collectText(node);
      if (!isBlock && !raw.includes('\n')) return;   // 行内代码不挂行号

      const lines = raw.replace(/\n+$/, '').split('\n').length;
      const gutter = Array.from({ length: lines }, (_, i) => i + 1).join('\n');
      const ln = {
        type: 'element', tagName: 'span',
        properties: { className: ['ln'], ariaHidden: 'true' },
        children: [{ type: 'text', value: gutter }],
      };
      const body = {
        type: 'element', tagName: 'span',
        properties: { className: ['code-body'] },
        children: node.children,
      };
      node.children = [ln, body];
    });
  };
}

/** 表格外面套一层可横向滚动的壳：一张 8 列的表不该把气泡撑破。 */
function rehypeTableWrap(): (tree: AnyNode) => void {
  return (tree) => {
    visit(tree, { type: 'element', tagName: 'table' }, (node: AnyNode, index: number | undefined, parent: AnyNode | undefined) => {
      if (!parent || typeof index !== 'number') return;
      parent.children[index] = {
        type: 'element', tagName: 'div',
        properties: { className: ['md-table-wrap'] },
        children: [node],
      };
    });
  };
}

/** 链接一律新窗口打开（Electron 里就是外部浏览器），别把应用自己导航走了。 */
function rehypeExternalLinks(): (tree: AnyNode) => void {
  return (tree) => {
    visit(tree, { type: 'element', tagName: 'a' }, (node: AnyNode) => {
      node.properties = {
        ...(node.properties ?? {}),
        target: '_blank',
        rel: 'noopener noreferrer',
      };
    });
  };
}

/**
 * unified 管道单例（lazy）：markdown → HTML 字符串。
 * [v1.0.23.15] rehype-highlight detect:false——实测自动语言检测贡献 ~70% 解析成本
 * （每段未标语言的代码块对所有注册语言评分）。未标注语言 = 不高亮，标了语言的照常。
 */
let _processor: any = null;
function getProcessor(): any {
  if (!_processor) {
    _processor = unified()
      .use(remarkParse)
      .use(remarkGfm)
      .use(remarkMath)
      .use(remarkBreaks)
      .use(remarkRehype)
      .use(rehypeHighlight as any, { detect: false, ignoreMissing: true })
      .use(rehypeKatex as any, { throwOnError: false, errorColor: '#e0245e', strict: 'ignore' })
      .use(rehypeLineGutter as any)
      .use(rehypeTableWrap as any)
      .use(rehypeExternalLinks as any)
      .use(rehypeStringify as any);
  }
  return _processor;
}

/**
 * [v1.0.23.15] ★ HTML 结果 LRU 缓存——真正挡住重复解析的那道墙。
 * 缓存 key = normalizeMath 后的源文本；命中 = 零管道执行。
 * LRU 上限 300 条（Map 保持插入序，超限淘汰最久未用）。
 */
const _MD_HTML_CACHE = new Map<string, string>();
const _MD_CACHE_MAX = 300;

function mdToHtml(src: string): string {
  const hit = _MD_HTML_CACHE.get(src);
  if (hit !== undefined) {
    _MD_HTML_CACHE.delete(src);
    _MD_HTML_CACHE.set(src, hit);
    return hit;
  }
  const html = String(getProcessor().processSync(src));
  _MD_HTML_CACHE.set(src, html);
  if (_MD_HTML_CACHE.size > _MD_CACHE_MAX) {
    const oldest = _MD_HTML_CACHE.keys().next().value;
    if (oldest !== undefined) _MD_HTML_CACHE.delete(oldest);
  }
  return html;
}

export interface MarkdownProps {
  text: string;
}

/**
 * ★ memo 是必须的，不是优化癖。
 *
 * 流式期间每来一个 token 就重渲染一次气泡；KaTeX 解析一条公式要毫秒级。
 * 文本没变就不重算——而流式里文本每次都在变，那也只重算**这一颗**气泡。
 *
 * [v1.0.23.15] 双层墙：memo 挡「同一气泡重复渲染」，mdToHtml 缓存挡
 * 「同一文本跨挂载重复解析」。dangerouslySetInnerHTML 一次性设置，
 * 无 React 组件树开销（对比 ReactMarkdown 的组件渲染）。
 */
export const Markdown: React.FC<MarkdownProps> = React.memo(({ text }) => {
  const html = useMemo(() => mdToHtml(normalizeMath(text || '')), [text]);

  return (
    <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
  );
});

Markdown.displayName = 'Markdown';

export default Markdown;
