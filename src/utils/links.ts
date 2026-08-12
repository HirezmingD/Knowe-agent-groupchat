// src/utils/links.ts
// [v0.38] URL 识别工具（MessageBubble 链接渲染 & RecordsDrawer 链接分类共用）

import React from 'react';

// 裸 URL：http(s):// 开头，直到空白 / 引号 / 尖括号 / 中文右括号为止。
const URL_RE = /(https?:\/\/[^\s<>"'）】」』]+)/g;
// 尾随标点不计入链接（中英文句读、右括号等）。
const TRAILING_PUNCT_RE = /[。，、）】」』.,!?;:]+$/;

/** 抽取文本里所有 URL（去尾随标点、去重、保序）。 */
export function extractUrls(text: string): string[] {
  if (!text) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of text.match(URL_RE) || []) {
    const url = raw.replace(TRAILING_PUNCT_RE, '');
    if (url && !seen.has(url)) { seen.add(url); out.push(url); }
  }
  return out;
}

/**
 * 把纯文本里的裸 URL 转成 Markdown 链接 `[url](url)`，
 * 跳过【代码块 / 行内代码 / 已有 [text](url) 链接】，避免破坏已有格式。
 * 用于把 agent 文本交给 <Markdown> 之前的预处理。
 */
export function linkifyBareUrls(text: string): string {
  if (!text) return text;
  const GUARD_RE = /(```[\s\S]*?```|`[^`]*`|\[[^\]]*\]\([^)]*\))/g;
  const parts = text.split(GUARD_RE);
  return parts
    .map((seg, i) => {
      if (i % 2 === 1) return seg;                 // 受保护片段原样保留
      return seg.replace(URL_RE, (m) => {
        const trail = (m.match(TRAILING_PUNCT_RE) || [''])[0];
        const url = m.slice(0, m.length - trail.length);
        return `[${url}](${url})${trail}`;
      });
    })
    .join('');
}

/**
 * 把一行纯文本切成 React 节点，URL 变成可点击 <a>（新标签打开），其余原样。
 * 给**用户气泡**用：用户的字不走 Markdown（`**` 要原样显示），但 URL 仍要能点。
 */
export function linkifyLineToNodes(line: string, keyPrefix = ''): React.ReactNode[] {
  if (!line) return [line];
  const parts = line.split(URL_RE);
  return parts.map((part, i) => {
    if (/^https?:\/\//.test(part)) {
      const trail = (part.match(TRAILING_PUNCT_RE) || [''])[0];
      const url = part.slice(0, part.length - trail.length);
      return React.createElement(
        React.Fragment,
        { key: `${keyPrefix}a${i}` },
        React.createElement(
          'a',
          { href: url, target: '_blank', rel: 'noopener noreferrer' },
          url,
        ),
        trail,
      );
    }
    return React.createElement(React.Fragment, { key: `${keyPrefix}t${i}` }, part);
  });
}
