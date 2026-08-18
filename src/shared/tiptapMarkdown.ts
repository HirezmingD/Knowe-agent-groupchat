/**
 * tiptapMarkdown.ts — [v1.0.37.3] TipTap 文档与纯文本 markdown 的双向转换
 *
 * 为什么存在：输入框内核换成 TipTap 后，草稿（conv.draft）仍是纯文本 markdown
 * （store/协议/发送链零改动）。这两个纯函数负责：
 *   · tiptapDocToMarkdown —— 发送时/草稿写回：编辑器文档 JSON → markdown 文本
 *   · markdownToTiptapDoc —— 草稿恢复：markdown 文本 → 编辑器文档 JSON
 *
 * 只认本版本开放的节点子集（PRD R2 + R1）：
 *   paragraph / heading(1-3) / bold / italic / bulletList / orderedList /
 *   codeBlock / mention / hardBreak。未知节点降级为取文本（防御，不丢字）。
 *
 * markdown → JSON 是宽松解析：支持序列化子集 + 用户手写的常见 markdown；
 * 解析不了的保持为普通文本（渲染层本来就能显示 markdown 原文）。
 */

interface TipTapNode {
  type: string;
  text?: string;
  attrs?: Record<string, unknown>;
  content?: TipTapNode[];
  marks?: Array<{ type: string; attrs?: Record<string, unknown> }>;
}

/** 可选的成员匹配表：@名字 → 转 mention 节点（反序列化用）。 */
export interface MentionMatcher {
  label: string;
  id: string;
}

function inlineToMarkdown(node: TipTapNode): string {
  if (node.type === 'text') {
    let out = node.text ?? '';
    for (const mark of node.marks ?? []) {
      if (mark.type === 'bold') out = `**${out}**`;
      else if (mark.type === 'italic') out = `*${out}*`;
      else if (mark.type === 'code') out = `\`${out}\``;
    }
    return out;
  }
  if (node.type === 'mention') {
    const label = String(node.attrs?.label ?? '');
    return label ? `@${label}` : '';
  }
  if (node.type === 'hardBreak') return '\n';
  // 未知内联节点：递归取内容文本（不丢字）。
  return (node.content ?? []).map(inlineToMarkdown).join('');
}

function blockToMarkdown(node: TipTapNode): string {
  switch (node.type) {
    case 'paragraph': {
      const text = (node.content ?? []).map(inlineToMarkdown).join('');
      return text;
    }
    case 'heading': {
      const level = Math.min(3, Math.max(1, Number(node.attrs?.level ?? 1)));
      const text = (node.content ?? []).map(inlineToMarkdown).join('');
      return `${'#'.repeat(level)} ${text}`;
    }
    case 'bulletList':
      return (node.content ?? [])
        .map((item) => `- ${(item.content ?? []).map(inlineToMarkdown).join('')}`)
        .join('\n');
    case 'orderedList': {
      let n = 1;
      return (node.content ?? [])
        .map((item) => `${n++}. ${(item.content ?? []).map(inlineToMarkdown).join('')}`)
        .join('\n');
    }
    case 'codeBlock': {
      const lang = node.attrs?.language ? String(node.attrs.language) : '';
      const code = (node.content ?? []).map((t) => t.text ?? '').join('');
      return `\`\`\`${lang}\n${code}\n\`\`\``;
    }
    default: {
      // 未知块节点：降级为段落文本。
      return (node.content ?? []).map(inlineToMarkdown).join('');
    }
  }
}

/** 编辑器文档 JSON → markdown 纯文本。 */
export function tiptapDocToMarkdown(doc: TipTapNode): string {
  const blocks = (doc.content ?? [])
    .map(blockToMarkdown)
    .filter((line) => line !== '' || line.includes('\n'));
  return blocks.join('\n\n').trim();
}

/* ═══════════════ 反序列化（markdown → 文档 JSON）═══════════════ */

const HEADING_RE = /^(#{1,3})\s+(.*)$/;
const BULLET_RE = /^[-*]\s+(.*)$/;
const ORDERED_RE = /^(\d+)[.)]\s+(.*)$/;
const FENCE_RE = /^```(\w*)\s*$/;

/** 内联 markdown 的极简解析：**bold**、*italic*、`code`、@名字。 */
function parseInline(text: string, members: MentionMatcher[]): TipTapNode[] {
  const nodes: TipTapNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|@[^\s，。！？、,;；:：()（）\[\]{}]+)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      nodes.push({ type: 'text', text: text.slice(last, m.index) });
    }
    const token = m[0];
    if (token.startsWith('**') && token.endsWith('**')) {
      nodes.push({
        type: 'text', text: token.slice(2, -2),
        marks: [{ type: 'bold' }],
      });
    } else if (token.startsWith('*') && token.endsWith('*') && token.length > 2) {
      nodes.push({
        type: 'text', text: token.slice(1, -1),
        marks: [{ type: 'italic' }],
      });
    } else if (token.startsWith('`') && token.endsWith('`') && token.length > 2) {
      nodes.push({
        type: 'text', text: token.slice(1, -1),
        marks: [{ type: 'code' }],
      });
    } else if (token.startsWith('@')) {
      const label = token.slice(1);
      const hit = members.find((mem) => mem.label === label || mem.id === label);
      if (hit) {
        nodes.push({ type: 'mention', attrs: { id: hit.id, label: hit.label } });
      } else {
        nodes.push({ type: 'text', text: token });
      }
    }
    last = m.index + token.length;
  }
  if (last < text.length) nodes.push({ type: 'text', text: text.slice(last) });
  return nodes;
}

/** markdown 纯文本 → 编辑器文档 JSON。宽松解析，未知语法保持文本。 */
export function markdownToTiptapDoc(md: string, members: MentionMatcher[] = []): TipTapNode {
  const lines = md.split('\n');
  const content: TipTapNode[] = [];
  let i = 0;

  const pushText = (text: string): void => {
    const trimmed = text.trim();
    if (!trimmed) return;
    content.push({ type: 'paragraph', content: parseInline(text, members) });
  };

  while (i < lines.length) {
    const line = lines[i] ?? '';

    const heading = HEADING_RE.exec(line);
    if (heading) {
      const level = (heading[1] ?? '#').length;
      content.push({
        type: 'heading',
        attrs: { level },
        content: parseInline(heading[2] ?? '', members),
      });
      i += 1;
      continue;
    }

    const bullet = BULLET_RE.exec(line);
    if (bullet) {
      const items: TipTapNode[] = [];
      while (i < lines.length) {
        const b = BULLET_RE.exec(lines[i] ?? '');
        if (!b) break;
        items.push({ type: 'listItem', content: parseInline(b[1] ?? '', members) });
        i += 1;
      }
      content.push({ type: 'bulletList', content: items });
      continue;
    }

    const ordered = ORDERED_RE.exec(line);
    if (ordered) {
      const items: TipTapNode[] = [];
      while (i < lines.length) {
        const o = ORDERED_RE.exec(lines[i] ?? '');
        if (!o) break;
        items.push({ type: 'listItem', content: parseInline(o[2] ?? '', members) });
        i += 1;
      }
      content.push({ type: 'orderedList', content: items });
      continue;
    }

    const fence = FENCE_RE.exec(line);
    if (fence) {
      const codeLines: string[] = [];
      const lang = fence[1] ?? '';
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i] ?? '')) {
        codeLines.push(lines[i] ?? '');
        i += 1;
      }
      i += 1; // 跳过闭合围栏
      content.push({
        type: 'codeBlock',
        attrs: lang ? { language: lang } : {},
        content: [{ type: 'text', text: codeLines.join('\n') }],
      });
      continue;
    }

    // 普通行：累积连续非空行作为一个段落（保留换行由渲染层 remark-breaks 处理）。
    let text = line;
    let j = i + 1;
    while (j < lines.length && (lines[j] ?? '').trim() !== '' && !BLOCK_LINE_RE.test(lines[j] ?? '')) {
      text += '\n' + (lines[j] ?? '');
      j += 1;
    }
    pushText(text);
    i = j;
  }

  return { type: 'doc', content };
}

const BLOCK_LINE_RE = /^(#{1,3}\s|[-*]\s|\d+[.)]\s|```)/;
