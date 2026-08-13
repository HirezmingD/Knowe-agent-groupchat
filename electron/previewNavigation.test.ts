/** Preview navigation boundary tests. */
import { describe, expect, it } from 'vitest';
import {
  isAllowedPreviewFrameUrl,
  isPreviewTopLevelUrlUnknown,
  isSafeExternalPreviewUrl,
  parsePreviewNavigationDetails,
} from './previewNavigation';

const CONTROL_P1 = 'http://127.0.0.1:8081/preview/tree/project-a/index.html';
const LEGACY_P1 = 'http://p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.preview.localhost:8081/preview/tree/project-a/index.html';

describe('preview navigation runtime boundary', () => {
  it.each([undefined, null, 0, {}, [], true, ''])('fails closed for %p', (value) => {
    expect(isAllowedPreviewFrameUrl(value)).toBe(false);
  });

  it('allows only the fixed empty/srcdoc preview documents', () => {
    expect(isAllowedPreviewFrameUrl('about:blank')).toBe(true);
    expect(isAllowedPreviewFrameUrl('about:srcdoc#x')).toBe(true);
  });

  it('denies the retired preview tree, local control URLs and privileged schemes', () => {
    expect(isAllowedPreviewFrameUrl(CONTROL_P1, 'about:blank')).toBe(false);
    expect(isAllowedPreviewFrameUrl(LEGACY_P1, 'about:blank')).toBe(false);
    expect(isAllowedPreviewFrameUrl('about:srcdoc-escape', 'about:srcdoc')).toBe(false);
    expect(isAllowedPreviewFrameUrl('data:text/html,<img src=https://example.com>', 'about:srcdoc')).toBe(false);
    expect(isAllowedPreviewFrameUrl('blob:null/example', 'about:srcdoc')).toBe(false);
    expect(isAllowedPreviewFrameUrl('file:///tmp/a.html', 'about:srcdoc')).toBe(false);
    expect(isAllowedPreviewFrameUrl('javascript:alert(1)', 'about:srcdoc')).toBe(false);
    expect(isAllowedPreviewFrameUrl('https://example.com/', 'about:srcdoc')).toBe(false);
  });

  it('only opens non-local HTTP(S)/mailto links outside Electron', () => {
    expect(isSafeExternalPreviewUrl('https://example.com/docs')).toBe(true);
    expect(isSafeExternalPreviewUrl('mailto:test@example.com')).toBe(true);
    expect(isSafeExternalPreviewUrl(CONTROL_P1)).toBe(false);
    expect(isSafeExternalPreviewUrl(LEGACY_P1)).toBe(false);
    expect(isSafeExternalPreviewUrl('file:///tmp/a')).toBe(false);
    expect(isSafeExternalPreviewUrl('javascript:alert(1)')).toBe(false);
  });

  it('narrows unknown Electron details without throwing', () => {
    expect(parsePreviewNavigationDetails(undefined)).toBeNull();
    expect(parsePreviewNavigationDetails({ isMainFrame: false, url: LEGACY_P1, frame: {} })).toEqual({
      mainFrame: false,
      url: LEGACY_P1,
      currentFrameUrl: undefined,
    });
  });

  it('compares a top-level URL with the exact preview entry', () => {
    expect(isPreviewTopLevelUrlUnknown('file:///app/preview.html?x=1', 'file:///app/preview.html')).toBe(true);
    expect(isPreviewTopLevelUrlUnknown('file:///app/index.html', 'file:///app/preview.html')).toBe(false);
    expect(isPreviewTopLevelUrlUnknown(undefined, 'file:///app/preview.html')).toBe(false);
  });
});
