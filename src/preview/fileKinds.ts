/** 独立预览窗口的文件分类与展示函数；保持无状态、无副作用。 */

import type { PreviewFilePayload } from '../shared/bridge';
import i18n from '../i18n';

export type PreviewKind =
  | 'html'
  | 'image'
  | 'pdf'
  | 'docx'
  | 'sheet'
  | 'pptx'
  | 'code'
  | 'markdown'
  | 'text'
  | 'file';

const IMAGE_EXTS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif',
]);
const CODE_EXTS = new Set([
  'js', 'jsx', 'mjs', 'cjs', 'ts', 'tsx', 'mts', 'cts',
  'py', 'pyi', 'java', 'kt', 'kts', 'c', 'h', 'cc', 'cpp', 'cxx', 'hpp',
  'cs', 'fs', 'fsx', 'vb', 'go', 'rs', 'rb', 'php', 'swift', 'dart',
  'scala', 'lua', 'r', 'pl', 'pm', 'ex', 'exs', 'erl', 'hrl', 'clj',
  'cljs', 'cljc', 'groovy', 'gql', 'graphql', 'proto', 'sql', 'sh', 'bash',
  'zsh', 'fish', 'ps1', 'bat', 'cmd', 'yaml', 'yml', 'toml', 'xml', 'css',
  'scss', 'sass', 'less', 'ini', 'conf', 'cfg', 'vue', 'svelte', 'astro',
  'cmake', 'json', 'jsonc', 'json5',
]);
const TEXT_EXTS = new Set(['txt', 'log', 'csv', 'tsv']);
const CODE_FILENAMES = new Set([
  'dockerfile', 'makefile', 'cmakelists.txt', 'jenkinsfile', 'procfile',
  'gemfile', 'rakefile', 'vagrantfile', '.gitignore', '.gitattributes',
  '.editorconfig', '.npmrc', '.nvmrc', '.prettierrc', '.eslintrc',
]);
const VALID_DECLARED = new Set<PreviewKind>([
  'html', 'image', 'pdf', 'docx', 'sheet', 'pptx',
  'code', 'markdown', 'text', 'file',
]);

/** 文件名或路径的最后一段。 */
export function baseName(pathOrName: string): string {
  const normalized = pathOrName.replace(/\\/g, '/');
  const slash = normalized.lastIndexOf('/');
  return slash >= 0 ? normalized.slice(slash + 1) : normalized;
}

/** 小写、不带点的扩展名；没有则返回空串。 */
export function extOf(pathOrName: string): string {
  const name = baseName(pathOrName);
  const dot = name.lastIndexOf('.');
  return dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toLowerCase() : '';
}

function extensionOf(file: PreviewFilePayload): string {
  return (file.ext || extOf(file.name || file.path || '')).replace(/^\./, '').toLowerCase();
}

/**
 * 文件预览分类。
 *
 * 文本与表格的边界按产品规则固定：CSV/TSV/TXT/LOG 一律文本，XLSX 为表格；
 * 旧式二进制 XLS 不交给 OOXML 解析器，明确降级到通用文件卡片。HTML 不落入代码预览，
 * JSON 明确落入代码预览。
 */
export function kindOf(file: PreviewFilePayload): PreviewKind {
  const ext = extensionOf(file);
  const filename = baseName(file.name || file.path || '').toLowerCase();
  if (TEXT_EXTS.has(ext)) return 'text';
  if (ext === 'xls') return 'file';
  if (ext === 'xlsx') return 'sheet';
  if (ext === 'html' || ext === 'htm') return 'html';
  if (ext === 'md' || ext === 'markdown' || ext === 'mdown' || ext === 'mkd') return 'markdown';
  if (IMAGE_EXTS.has(ext)) return 'image';
  if (ext === 'pdf') return 'pdf';
  if (ext === 'docx') return 'docx';
  if (ext === 'pptx') return 'pptx';
  if (CODE_EXTS.has(ext) || CODE_FILENAMES.has(filename)) return 'code';

  const declared = (file.kind || '').trim().toLowerCase() as PreviewKind;
  return VALID_DECLARED.has(declared) ? declared : 'file';
}

/** 面向用户的格式标签。 */
export function typeLabel(file: PreviewFilePayload): string {
  const ext = extensionOf(file).toUpperCase();
  if (ext) return ext;
  switch (kindOf(file)) {
    case 'image': return i18n.t('common.01');
    case 'docx': return 'WORD';
    case 'sheet': return i18n.t('file.kinds.03');
    case 'pptx': return 'PPT';
    case 'markdown': return 'MD';
    case 'html': return 'HTML';
    case 'pdf': return 'PDF';
    case 'code': return i18n.t('file.kinds.01');
    case 'text': return i18n.t('file.kinds.02');
    default: return i18n.t('common.02');
  }
}

/** 人类可读的字节数。 */
export function humanBytes(bytes: number | undefined): string {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const shown = value >= 10 || Number.isInteger(value) ? Math.round(value) : value.toFixed(1);
  return `${shown} ${units[unit] ?? 'KB'}`;
}
