/** Preview origin, project and navigation boundary tests. */
import { describe, expect, it } from 'vitest';
import {
  isolatedPreviewOrigin,
  isAllowedPreviewFrameUrl,
  isPreviewControlRequest,
  isPreviewTopLevelUrlUnknown,
  isSafeExternalPreviewUrl,
  parsePreviewNavigationDetails,
  previewTokenForProject,
  previewTreeProject,
} from './previewNavigation';

const origin = (project: string): string => isolatedPreviewOrigin(project);
const tree = (project: string, path = 'index.html'): string => (
  `${origin(project)}/preview/tree/${encodeURIComponent(project)}/${path}`
);
const P1 = tree('project-a');
const P1_CHILD = `${origin('project-a')}/assets/a.png`;
const P2 = tree('project-b');
const CONTROL_P1 = 'http://127.0.0.1:8081/preview/tree/project-a/index.html';

describe('preview navigation runtime boundary', () => {
  it.each([undefined, null, 0, {}, [], true, ''])('fails closed for %p', (value) => {
    expect(isAllowedPreviewFrameUrl(value)).toBe(false);
    expect(previewTreeProject(value)).toBeNull();
  });

  it('derives one deterministic isolated origin per project', () => {
    expect(previewTokenForProject('project-a')).toMatch(/^[0-9a-f]{32}$/);
    expect(origin('project-a')).not.toBe(origin('project-b'));
    expect(previewTreeProject(P1)).toBe('project-a');
  });

  it('allows initial isolated entry and same-project root/relative navigation', () => {
    expect(isAllowedPreviewFrameUrl('about:blank')).toBe(true);
    expect(isAllowedPreviewFrameUrl('about:srcdoc#x')).toBe(true);
    expect(isAllowedPreviewFrameUrl(P1, 'about:blank')).toBe(true);
    expect(isAllowedPreviewFrameUrl(P1_CHILD, P1)).toBe(true);
    expect(isAllowedPreviewFrameUrl(`${origin('project-a')}/next/page.html`, P1)).toBe(true);
  });

  it('denies control origin, cross-project origin, malformed token and privileged schemes', () => {
    expect(isAllowedPreviewFrameUrl(CONTROL_P1, 'about:blank')).toBe(false);
    expect(isAllowedPreviewFrameUrl(P2, P1)).toBe(false);
    expect(isAllowedPreviewFrameUrl('file:///tmp/a.html', P1)).toBe(false);
    expect(isAllowedPreviewFrameUrl('javascript:alert(1)', P1)).toBe(false);
    expect(isAllowedPreviewFrameUrl('https://example.com/', P1)).toBe(false);
    expect(isAllowedPreviewFrameUrl(
      'http://p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.preview.localhost:8081/preview/tree/project-a/index.html',
      'about:blank',
    )).toBe(false);
    expect(isAllowedPreviewFrameUrl(P1_CHILD, 'about:srcdoc')).toBe(false);
  });

  it('blocks every control-host request initiated by an isolated project page', () => {
    expect(isPreviewControlRequest('http://127.0.0.1:8081/settings', origin('project-a'))).toBe(true);
    expect(isPreviewControlRequest('http://127.0.0.1:8081/history', P1)).toBe(true);
    expect(isPreviewControlRequest('http://127.0.0.1:8081/preview?project_id=project-b&path=x', P1)).toBe(true);
    expect(isPreviewControlRequest('http://127.0.0.1:8081/settings', 'file:///app/preview.html')).toBe(false);
    expect(isPreviewControlRequest('https://example.com/api', P1)).toBe(false);
    expect(isPreviewControlRequest(undefined, P1)).toBe(false);
  });

  it('only opens non-local HTTP(S)/mailto links outside Electron', () => {
    expect(isSafeExternalPreviewUrl('https://example.com/docs')).toBe(true);
    expect(isSafeExternalPreviewUrl('mailto:test@example.com')).toBe(true);
    expect(isSafeExternalPreviewUrl(CONTROL_P1)).toBe(false);
    expect(isSafeExternalPreviewUrl(P1)).toBe(false);
    expect(isSafeExternalPreviewUrl('file:///tmp/a')).toBe(false);
    expect(isSafeExternalPreviewUrl('javascript:alert(1)')).toBe(false);
  });

  it('narrows unknown Electron details without throwing', () => {
    expect(parsePreviewNavigationDetails(undefined)).toBeNull();
    expect(parsePreviewNavigationDetails({ isMainFrame: false, url: P1, frame: {} })).toEqual({
      mainFrame: false,
      url: P1,
      currentFrameUrl: undefined,
    });
  });

  it('compares a top-level URL with the exact preview entry', () => {
    expect(isPreviewTopLevelUrlUnknown('file:///app/preview.html?x=1', 'file:///app/preview.html')).toBe(true);
    expect(isPreviewTopLevelUrlUnknown('file:///app/index.html', 'file:///app/preview.html')).toBe(false);
    expect(isPreviewTopLevelUrlUnknown(undefined, 'file:///app/preview.html')).toBe(false);
  });
});
