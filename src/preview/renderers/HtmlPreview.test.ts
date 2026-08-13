import { describe, expect, it } from 'vitest';

import { sandboxedHtmlSource } from './HtmlPreview';


describe('sandboxedHtmlSource', () => {
  it('places the policy before every project-controlled byte', () => {
    const result = sandboxedHtmlSource(
      '<!doctype html><html><head><script src="https://evil.invalid/x.js"></script></head></html>',
    );
    expect(result.indexOf('Content-Security-Policy')).toBeGreaterThan(-1);
    expect(result.indexOf('Content-Security-Policy')).toBeLessThan(result.indexOf('<script'));
    expect(result).toContain("connect-src 'none'");
    expect(result).toContain("frame-src 'none'");
    expect(result).toContain("script-src 'none'");
  });

  it('creates an early head when the document omitted one', () => {
    const result = sandboxedHtmlSource('<html><body>safe preview</body></html>');
    expect(result).toMatch(/^<!doctype html><html><head><meta http-equiv="Content-Security-Policy"/i);
  });

  it('places the policy before a fragment body', () => {
    const result = sandboxedHtmlSource('<img src="https://evil.invalid/pixel">');
    expect(result).toMatch(/^<!doctype html><html><head><meta http-equiv="Content-Security-Policy"/i);
    expect(result.indexOf('Content-Security-Policy')).toBeLessThan(result.indexOf('<img'));
  });

  it('cannot hide the policy with a fake head inside a comment', () => {
    const attacker = '<!-- <head> --><script>fetch("http://127.0.0.1:8081/shutdown")</script>';
    const result = sandboxedHtmlSource(attacker);
    expect(result.indexOf('Content-Security-Policy')).toBeLessThan(result.indexOf('<!-- <head> -->'));
    expect(result).toContain("script-src 'none'");
  });
});
