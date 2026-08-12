/** [v1.0.24.5] runtimeFetch HTTP 认证封装测试。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { __resetRuntimeAuthForTests, runtimeFetch } from './runtimeFetch';

beforeEach(() => {
  __resetRuntimeAuthForTests();
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('runtimeFetch auth header injection', () => {
  it('injects X-Knowe-Runtime-Token when the bridge provides a token', async () => {
    vi.stubGlobal('window', {
      knowe: { getRuntimeToken: async () => 'tok_abc123' },
    });
    const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await runtimeFetch('http://127.0.0.1:8081/settings');
    const call = mockFetch.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined];
    const headers = new Headers(call[1]?.headers);
    expect(headers.get('X-Knowe-Runtime-Token')).toBe('tok_abc123');
  });

  it('keeps caller headers and adds token on top', async () => {
    vi.stubGlobal('window', {
      knowe: { getRuntimeToken: async () => 'tok_xyz' },
    });
    const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await runtimeFetch('http://127.0.0.1:8081/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const call = mockFetch.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined];
    const init = call[1];
    const headers = new Headers(init?.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-Knowe-Runtime-Token')).toBe('tok_xyz');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBe('{}');
  });

  it('fetches without a token header when the bridge is absent (browser-only dev)', async () => {
    vi.stubGlobal('window', {});
    const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await runtimeFetch('http://127.0.0.1:8081/settings');
    const call = mockFetch.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined];
    const headers = new Headers(call[1]?.headers);
    expect(headers.get('X-Knowe-Runtime-Token')).toBeNull();
  });

  it('caches the token across requests (single IPC round-trip)', async () => {
    const getToken = vi.fn().mockResolvedValue('tok_cached');
    vi.stubGlobal('window', { knowe: { getRuntimeToken: getToken } });
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(new Response('{}', { status: 200 }));
    await runtimeFetch('http://127.0.0.1:8081/a');
    await runtimeFetch('http://127.0.0.1:8081/b');
    expect(getToken).toHaveBeenCalledTimes(1);
  });

  it('propagates fetch errors untouched', async () => {
    vi.stubGlobal('window', {
      knowe: { getRuntimeToken: async () => 'tok_err' },
    });
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new TypeError('network down'));
    await expect(runtimeFetch('http://127.0.0.1:8081/settings')).rejects.toThrow('network down');
  });
});
