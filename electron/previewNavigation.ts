/** Runtime-safe preview navigation policy for Electron boundaries. */

import { createHash } from 'node:crypto';

/** A narrowed, side-effect-free decision returned by the external event parser. */
export interface PreviewNavigationDecision {
  readonly mainFrame: boolean;
  readonly url: unknown;
  readonly currentFrameUrl: unknown;
}

const HTTP_PORT = String(process.env.KNOWE_HEALTH_PORT || '8081');
const CONTROL_HOST = `127.0.0.1:${HTTP_PORT}`;
const PREVIEW_HOST_RE = /^p-([0-9a-f]{32})\.preview\.localhost(?::(\d{1,5}))?$/i;
const TREE_PREFIX = '/preview/tree/';

type ParsedPreviewUrl = {
  readonly kind: 'control' | 'isolated';
  readonly token?: string;
  readonly project?: string;
  readonly url: URL;
};

export function previewTokenForProject(projectId: string): string {
  return createHash('sha256').update(projectId, 'utf8').digest('hex').slice(0, 32);
}

export function isolatedPreviewOrigin(projectId: string): string {
  return `http://p-${previewTokenForProject(projectId)}.preview.localhost:${HTTP_PORT}`;
}

function projectFromTreePath(pathname: string): string | null {
  if (!pathname.startsWith(TREE_PREFIX)) return null;
  const rest = pathname.slice(TREE_PREFIX.length);
  const slash = rest.indexOf('/');
  if (slash <= 0) return null;
  try {
    const project = decodeURIComponent(rest.slice(0, slash));
    return project.length > 0 ? project : null;
  } catch {
    return null;
  }
}

function parsePreviewUrl(raw: unknown): ParsedPreviewUrl | null {
  if (typeof raw !== 'string' || raw.length === 0) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== 'http:' || url.port !== HTTP_PORT) return null;

    if (url.host === CONTROL_HOST) {
      const project = projectFromTreePath(url.pathname);
      // /preview/tree/ paths on the control host are isolated preview resources,
      // not control endpoints — allow them through the frame policy.
      return project ? { kind: 'isolated', project, url } : null;
    }

    const match = PREVIEW_HOST_RE.exec(url.host);
    if (!match) return null;
    if ((match[2] || '') !== HTTP_PORT) return null;
    const token = match[1]!.toLowerCase();
    const project = projectFromTreePath(url.pathname) || undefined;
    if (project && previewTokenForProject(project) !== token) return null;
    return { kind: 'isolated', token, project, url };
  } catch {
    return null;
  }
}

export function previewTreeProject(raw: unknown): string | null {
  return parsePreviewUrl(raw)?.project || null;
}

export function isPreviewTopLevelUrlUnknown(raw: unknown, expectedRaw: unknown): boolean {
  if (typeof raw !== 'string' || raw.length === 0) return false;
  if (typeof expectedRaw !== 'string' || expectedRaw.length === 0) return false;
  try {
    const actual = new URL(raw);
    const expected = new URL(expectedRaw);
    return actual.protocol === expected.protocol
      && actual.host === expected.host
      && decodeURIComponent(actual.pathname) === decodeURIComponent(expected.pathname);
  } catch {
    return false;
  }
}

export function isAllowedPreviewFrameUrl(
  raw: unknown,
  currentRaw: unknown = '',
): boolean {
  if (typeof raw !== 'string' || raw.length === 0) return false;
  const currentUrl = typeof currentRaw === 'string' ? currentRaw : '';

  if (raw === 'about:blank' || raw.startsWith('about:srcdoc')) return true;
  // Allow data: and blob: URLs — inline content that makes no network requests.
  if (raw.startsWith('data:') || raw.startsWith('blob:')) return true;
  if (currentUrl.startsWith('about:srcdoc')) return false;

  const target = parsePreviewUrl(raw);
  if (!target || target.kind !== 'isolated') return false;

  // The first real navigation must carry an explicit project in the tree path so the
  // backend can validate and bind the otherwise opaque origin token.
  if (!currentUrl || currentUrl === 'about:blank') return !!target.project;

  const current = parsePreviewUrl(currentUrl);
  return current?.kind === 'isolated'
    && current.token === target.token;
}

/** Project pages may use ordinary HTTP(S), but never Knowe's local control origin. */
export function isPreviewControlRequest(rawTarget: unknown, rawInitiator: unknown): boolean {
  if (typeof rawTarget !== 'string' || typeof rawInitiator !== 'string') return false;
  try {
    const target = new URL(rawTarget);
    if (target.protocol !== 'http:' || target.host !== CONTROL_HOST) return false;
    const initiator = parsePreviewUrl(rawInitiator);
    return initiator?.kind === 'isolated';
  } catch {
    return false;
  }
}

/** External links may leave through the system browser, but local/control origins may not. */
export function isSafeExternalPreviewUrl(raw: unknown): boolean {
  if (typeof raw !== 'string' || raw.length === 0) return false;
  try {
    const url = new URL(raw);
    if (url.protocol === 'mailto:') return true;
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
    if (isLoopbackHostname(url.hostname)) return false;
    return true;
  } catch {
    return false;
  }
}

function isLoopbackHostname(raw: string): boolean {
  const host = raw.trim().toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (host === 'localhost' || host.endsWith('.localhost') || host === '::1' || host === '::') return true;
  if (
    host === '0.0.0.0'
    || host.startsWith('::ffff:127.')
    || /^::ffff:7f[0-9a-f]{2}(?::|$)/.test(host)
  ) return true;
  const octets = host.split('.');
  return octets.length === 4
    && octets.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255)
    && Number(octets[0]) === 127;
}

export type PreviewFrameNavigationAction = 'allow' | 'open-external' | 'deny';

/** One policy point for in-frame navigation: same project stays embedded, safe web links leave. */
export function previewFrameNavigationAction(
  raw: unknown,
  currentRaw: unknown = '',
): PreviewFrameNavigationAction {
  if (isAllowedPreviewFrameUrl(raw, currentRaw)) return 'allow';
  if (isSafeExternalPreviewUrl(raw)) return 'open-external';
  return 'deny';
}

export function extractFrameUrl(frame: unknown): unknown {
  if (!frame || typeof frame !== 'object') return '';
  return (frame as Record<string, unknown>).url;
}

export function parsePreviewNavigationDetails(details: unknown): PreviewNavigationDecision | null {
  if (!details || typeof details !== 'object') return null;
  const value = details as Record<string, unknown>;
  return {
    mainFrame: value.isMainFrame === true,
    url: value.url,
    currentFrameUrl: extractFrameUrl(value.frame),
  };
}
