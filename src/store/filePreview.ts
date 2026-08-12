/**
 * filePreview.ts — 独立预览窗口共用的文件身份、解析与只读取数函数。
 *
 * 主窗口只把聊天卡片保存的原始身份送进 Electron；真正的路径恢复、重试与字节读取
 * 全由独立预览 renderer 完成，因此主窗口关闭后既有标签仍可继续工作。
 */

import type { PreviewFilePayload } from '../shared/bridge';
import { runtimeHttpBase } from '../shared/runtimeEndpoints';
import { runtimeFetch } from '../shared/runtimeFetch';
import type { ProducedFile } from './state';
import i18n from '../i18n';

export const previewOrigin = (): string => runtimeHttpBase();

/** 后端解析后可能补充的字段；仍然只包含可序列化数据。 */
export type TrackableProducedFile = PreviewFilePayload & {
  renamed?: boolean;
  /** Canonical backend project id used to derive the isolated HTML origin. */
  resolved_project_id?: string;
};

/** 私聊文件实际属于父群工作区。 */
export function previewProjectId(projectId: string): string {
  if (!projectId.startsWith('dm:')) return projectId;
  const rest = projectId.slice(3);
  const separator = rest.indexOf(':');
  return separator > 0 && separator < rest.length - 1
    ? rest.slice(0, separator)
    : projectId;
}

function sourcePathOf(file: PreviewFilePayload): string {
  return (file.source_path || file.path).replace(/\\/g, '/');
}


const URL_SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]*:/;
const WINDOWS_DRIVE_RE = /^[A-Za-z]:/;

export interface RelativePreviewTarget {
  path: string;
  /** Raw URL fragment without the leading '#'; decode only at the rendered heading boundary. */
  fragment: string;
}

function splitRelativeHref(rawHref: string): {
  pathPart: string;
  fragment: string;
} {
  const hashAt = rawHref.indexOf('#');
  const beforeHash = hashAt >= 0 ? rawHref.slice(0, hashAt) : rawHref;
  const fragment = hashAt >= 0 ? rawHref.slice(hashAt + 1) : '';
  const queryAt = beforeHash.indexOf('?');
  return {
    pathPart: queryAt >= 0 ? beforeHash.slice(0, queryAt) : beforeHash,
    fragment,
  };
}

function splitResourceReference(raw: string): { pathPart: string; suffix: string } {
  const queryAt = raw.indexOf('?');
  const hashAt = raw.indexOf('#');
  const indexes = [queryAt, hashAt].filter((value) => value >= 0);
  const cut = indexes.length > 0 ? Math.min(...indexes) : raw.length;
  return { pathPart: raw.slice(0, cut), suffix: raw.slice(cut) };
}

function decodedRelativePath(rawPath: string, label: string): string {
  try {
    return decodeURIComponent(rawPath).replace(/\\/g, '/');
  } catch {
    throw new Error(i18n.t('file.preview.badUrlEncode', { label }));
  }
}

/** Canonical project-relative path shared by resolve, tabs, Markdown links and images. */
export function canonicalPreviewPath(rawPath: string, label = i18n.t('file.preview.13')): string {
  const normalized = String(rawPath || '').trim().replace(/\\/g, '/');
  if (
    !normalized
    || normalized.includes('\u0000')
    || normalized.startsWith('/')
    || normalized.startsWith('//')
    || WINDOWS_DRIVE_RE.test(normalized)
    || URL_SCHEME_RE.test(normalized)
  ) {
    throw new Error(i18n.t('file.preview.badRelPath', { label }));
  }

  const segments: string[] = [];
  for (const segment of normalized.split('/')) {
    if (!segment || segment === '.') continue;
    if (segment === '..') {
      if (segments.length === 0) throw new Error(i18n.t('file.preview.escapeRoot', { label }));
      segments.pop();
      continue;
    }
    if (segment.includes('\u0000')) throw new Error(i18n.t('file.preview.badChars', { label }));
    segments.push(segment);
  }
  if (segments.length === 0) throw new Error(i18n.t('file.preview.notAFile', { label }));
  return segments.join('/');
}

/** Resolve a Markdown document link without leaving the project-relative namespace. */
export function resolveProjectRelativeTarget(
  currentFilePath: string,
  rawHref: string,
): RelativePreviewTarget {
  const href = String(rawHref || '').trim();
  if (!href) throw new Error(i18n.t('file.preview.16'));
  if (href.includes('\u0000')) throw new Error(i18n.t('file.preview.17'));
  const current = canonicalPreviewPath(currentFilePath, i18n.t('file.preview.09'));
  if (href.startsWith('#')) return { path: current, fragment: href.slice(1) };
  if (
    URL_SCHEME_RE.test(href)
    || href.startsWith('//')
    || href.startsWith('/')
    || href.startsWith('\\')
    || WINDOWS_DRIVE_RE.test(href)
  ) {
    throw new Error(i18n.t('file.preview.22'));
  }

  const { pathPart, fragment } = splitRelativeHref(href);
  if (pathPart.length === 0) return { path: current, fragment };
  const decoded = decodedRelativePath(pathPart, i18n.t('file.preview.15'));
  if (
    !decoded
    || decoded.includes('\u0000')
    || URL_SCHEME_RE.test(decoded)
    || decoded.startsWith('/')
    || decoded.startsWith('//')
    || WINDOWS_DRIVE_RE.test(decoded)
  ) {
    throw new Error(i18n.t('file.preview.18'));
  }

  const parent = current.split('/');
  parent.pop();
  return {
    path: canonicalPreviewPath([...parent, ...decoded.split('/')].join('/'), i18n.t('file.preview.15')),
    fragment,
  };
}

/** Resolve a Markdown image reference; query/hash suffixes are metadata, not filesystem paths. */
export function resolveMarkdownRelativePath(
  currentFilePath: string,
  rawReference: string,
): { path: string; suffix: string } {
  const reference = String(rawReference || '').trim();
  if (!reference) throw new Error(i18n.t('file.preview.06'));
  if (reference.includes('\u0000')) throw new Error(i18n.t('file.preview.07'));
  if (
    URL_SCHEME_RE.test(reference)
    || reference.startsWith('//')
    || reference.startsWith('/')
    || reference.startsWith('\\')
    || WINDOWS_DRIVE_RE.test(reference)
  ) {
    throw new Error(i18n.t('file.preview.02'));
  }

  const current = canonicalPreviewPath(currentFilePath, i18n.t('file.preview.08'));
  const { pathPart, suffix } = splitResourceReference(reference);
  const decoded = decodedRelativePath(pathPart, i18n.t('file.preview.05'));
  if (
    !decoded
    || decoded.includes('\u0000')
    || URL_SCHEME_RE.test(decoded)
    || decoded.startsWith('/')
    || decoded.startsWith('//')
    || WINDOWS_DRIVE_RE.test(decoded)
  ) {
    throw new Error(i18n.t('file.preview.01'));
  }

  const parent = current.split('/');
  parent.pop();
  return {
    path: canonicalPreviewPath([...parent, ...decoded.split('/')].join('/'), i18n.t('file.preview.05')),
    suffix,
  };
}

/** Stable identity for a relative document tab; fragments intentionally reuse the same tab. */
export function relativePreviewSourceKey(projectId: string, relativePath: string): string {
  return `relative:${JSON.stringify([previewProjectId(projectId), canonicalPreviewPath(relativePath)])}`;
}

export function comparablePreviewPath(rawPath: string): string | null {
  try {
    return canonicalPreviewPath(rawPath);
  } catch {
    return null;
  }
}

export function relativePreviewFile(relativePath: string): PreviewFilePayload {
  const path = canonicalPreviewPath(relativePath, i18n.t('file.preview.15'));
  const parts = path.split('/');
  return {
    path,
    source_path: path,
    name: parts[parts.length - 1] || path,
  };
}

/** 同一张历史卡片的稳定前端 key；重命名后仍指向原卡片。 */
export function previewSourceKey(projectId: string, file: PreviewFilePayload): string {
  const identity = file.file_id
    || `${Number.isFinite(file.bytes) ? file.bytes : ''}:${file.mtime_ns ?? file.mtime ?? ''}`;
  return `${previewProjectId(projectId)}\u0000${sourcePathOf(file)}\u0000${identity}`;
}

function appendIdentity(params: URLSearchParams, file: PreviewFilePayload): void {
  if (file.file_id) params.set('file_id', file.file_id);
  if (Number.isFinite(file.bytes)) params.set('bytes', String(file.bytes));
  if (Number.isFinite(file.mtime_ns)) params.set('mtime_ns', String(file.mtime_ns));
  if (file.mtime) params.set('mtime', file.mtime);
}

/** 原始字节地址；传文件对象时携带稳定身份，支持同目录重命名恢复。 */
export function previewUrl(
  projectId: string,
  pathOrFile: string | PreviewFilePayload,
): string {
  const path = typeof pathOrFile === 'string' ? pathOrFile : sourcePathOf(pathOrFile);
  const params = new URLSearchParams({ project_id: previewProjectId(projectId), path });
  if (typeof pathOrFile !== 'string') appendIdentity(params, pathOrFile);
  return `${previewOrigin()}/preview?${params.toString()}`;
}

async function sha256Hex(value: string): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error(i18n.t('file.preview.10'));
  }
  const digest = await globalThis.crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/**
 * HTML 预览：直接获取内容，用 srcdoc 渲染。
 * 不用 URL、不触发导航事件、不走 DNS/iframe 策略——最简路径。
 */
export async function fetchPreviewHtml(
  projectId: string,
  file: PreviewFilePayload,
): Promise<string> {
  const resolved = (file as TrackableProducedFile).resolved_project_id;
  const realProjectId = resolved || previewProjectId(projectId);
  return fetchPreviewText(realProjectId, file);
}

function humanizeStatus(status: number, serverMessage?: string): string {
  if (status === 404) {
    if (!serverMessage || serverMessage === i18n.t('file.preview.19')) {
      return i18n.t('file.preview.20');
    }
    return serverMessage;
  }
  if (status === 403) return serverMessage || i18n.t('file.preview.21');
  if (status === 413) return i18n.t('file.preview.12');
  if (status >= 500) return serverMessage || i18n.t('file.preview.04');
  return serverMessage || i18n.t('file.preview.fetchFailed', { status });
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json() as { error?: unknown };
    if (typeof body?.error === 'string') return humanizeStatus(response.status, body.error);
  } catch {
    // 非 JSON 错误体交给状态码兜底。
  }
  return humanizeStatus(response.status);
}

async function requestPreview(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await runtimeFetch(input, { cache: 'no-store', ...init });
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error(i18n.t('file.preview.23'));
    }
    throw error;
  }
}

interface ResolveResponse {
  ok: boolean;
  project_id: string;
  file: Partial<TrackableProducedFile> & { path: string; name: string };
}

function mergeResolvedFile(
  file: PreviewFilePayload,
  nextFile: ResolveResponse['file'],
  canonicalProjectId?: string,
): TrackableProducedFile {
  const merged: TrackableProducedFile = {
    ...file,
    ...nextFile,
    source_path: sourcePathOf(file),
    ...(canonicalProjectId ? { resolved_project_id: canonicalProjectId } : {}),
  };
  if (nextFile.ext && nextFile.ext !== file.ext) delete merged.kind;
  return merged;
}

/** 在具体渲染器挂载前恢复文件当前路径，并返回最新元数据。 */
export async function resolvePreviewFile(
  projectId: string,
  file: PreviewFilePayload,
): Promise<TrackableProducedFile> {
  const params = new URLSearchParams({
    project_id: previewProjectId(projectId),
    path: sourcePathOf(file),
  });
  appendIdentity(params, file);
  const response = await requestPreview(`${previewOrigin()}/preview/resolve?${params.toString()}`);
  if (!response.ok) throw new Error(await readError(response));
  const payload = await response.json() as ResolveResponse;
  if (!payload?.ok || !payload.file?.path || !payload.file?.name) {
    throw new Error(i18n.t('file.preview.03'));
  }
  return mergeResolvedFile(file, payload.file, payload.project_id);
}

/** 在系统文件管理器中定位文件；服务端仍执行沙箱和文件身份校验。 */
export async function revealFileInFolder(
  projectId: string,
  file: PreviewFilePayload,
): Promise<TrackableProducedFile> {
  const response = await requestPreview(`${previewOrigin()}/files/reveal`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: previewProjectId(projectId),
      file: { ...file, source_path: sourcePathOf(file) },
    }),
  });
  if (!response.ok) throw new Error(await readError(response));
  const payload = await response.json() as ResolveResponse;
  if (!payload?.ok || !payload.file?.path || !payload.file?.name) {
    throw new Error(i18n.t('file.preview.11'));
  }
  return mergeResolvedFile(file, payload.file, payload.project_id);
}

/** 取文本，失败时抛出已人话化的 Error。 */
export async function fetchPreviewText(
  projectId: string,
  pathOrFile: string | PreviewFilePayload,
): Promise<string> {
  const response = await requestPreview(previewUrl(projectId, pathOrFile));
  if (!response.ok) throw new Error(await readError(response));
  return response.text();
}

/** 取二进制；Office、PDF 等解析器只在预览 renderer 内调用。 */
export async function fetchPreviewArrayBuffer(
  projectId: string,
  pathOrFile: string | PreviewFilePayload,
): Promise<ArrayBuffer> {
  const response = await requestPreview(previewUrl(projectId, pathOrFile));
  if (!response.ok) throw new Error(await readError(response));
  return response.arrayBuffer();
}

/** 主窗口只发送原始身份，不提前 resolve，也不持有标签状态。 */
export async function openPreviewWindow(file: ProducedFile, projectId: string): Promise<void> {
  const realProjectId = previewProjectId(projectId);
  const sourceFile: TrackableProducedFile = {
    ...file,
    source_path: sourcePathOf(file),
  };
  const bridge = window.knowe;
  if (!bridge?.openPreview) throw new Error(i18n.t('file.preview.14'));
  await bridge.openPreview({
    projectId: realProjectId,
    sourceKey: previewSourceKey(realProjectId, sourceFile),
    file: sourceFile,
  });
}
