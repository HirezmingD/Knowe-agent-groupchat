// src/components/InlineMarkdown.tsx
// [v0.38.1 #7] 极简「内联」Markdown：只渲染 行内 强调/代码/链接，不碰段落/标题/列表/换行。
//
// 聊天记录摘要行原来直接显示 content，`**加粗**` 的星号会原文露出来。这里把常见的
// 行内标记渲染成对应的 <strong>/<em>/<code>/<del>/<a>，其余照旧当文字。
//
// 有意做得「轻」：不递归嵌套（摘要里几乎不会出现 **_套_**），一遍扫描即可，正则不回溯爆炸。

import React from 'react';

// 行内标记，按优先级排列（代码优先，避免把 `a*b` 里的星号当强调）。
const TOKEN_RE = new RegExp(
  [
    '`([^`]+)`',                 // 1 code
    '\\*\\*([^*]+)\\*\\*',       // 2 bold
    '__([^_]+)__',               // 3 bold(alt)
    '~~([^~]+)~~',               // 4 strike
    '\\*([^*]+)\\*',             // 5 italic
    '_([^_]+)_',                 // 6 italic(alt)
    '\\[([^\\]]+)\\]\\(([^)]+)\\)', // 7 text, 8 href
  ].join('|'),
  'g',
);

export function renderInlineMarkdown(text: string, keyPrefix = ''): React.ReactNode[] {
  if (!text) return [text];
  const out: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyPrefix}${i++}`;
    if (m[1] !== undefined) out.push(<code key={key}>{m[1]}</code>);
    else if (m[2] !== undefined) out.push(<strong key={key}>{m[2]}</strong>);
    else if (m[3] !== undefined) out.push(<strong key={key}>{m[3]}</strong>);
    else if (m[4] !== undefined) out.push(<del key={key}>{m[4]}</del>);
    else if (m[5] !== undefined) out.push(<em key={key}>{m[5]}</em>);
    else if (m[6] !== undefined) out.push(<em key={key}>{m[6]}</em>);
    else if (m[7] !== undefined) {
      // 链接：摘要里只显示文字（不可点，避免摘要行里误触跳转）——保留可读文字即可。
      out.push(<span key={key}>{m[7]}</span>);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** 内联 Markdown 组件：把行内标记渲染成对应元素，不产生块级结构。 */
const InlineMarkdown: React.FC<{ text: string }> = ({ text }) => (
  <>{renderInlineMarkdown(text)}</>
);

export default InlineMarkdown;
