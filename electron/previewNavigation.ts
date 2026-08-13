/** Runtime-safe preview navigation policy for Electron boundaries. */

/** A narrowed, side-effect-free decision returned by the external event parser. */
export interface PreviewNavigationDecision {
  readonly mainFrame: boolean;
  readonly url: unknown;
  readonly currentFrameUrl: unknown;
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
  _currentRaw: unknown = '',
): boolean {
  if (typeof raw !== 'string' || raw.length === 0) return false;

  if (raw === 'about:blank' || raw === 'about:srcdoc' || raw.startsWith('about:srcdoc#')) return true;
  // The renderer supplies preview documents through srcdoc only. The retired
  // /preview/tree HTTP surface, its Host labels, and CSP-resetting data/blob
  // navigations are never valid frame destinations.
  return false;
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
