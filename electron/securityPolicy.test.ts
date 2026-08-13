import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  configureRemoteDebugging,
  isAuthorizedRuntimeWebSocket,
  isTrustedTopLevelRuntimeFrame,
  sanitizeBackendEnvironment,
  stripRuntimeTokenHeader,
} from './securityPolicy';

describe('packaged file renderer fuse', () => {
  it('keeps loadFile module privileges enabled in both builder and verifier', () => {
    const builder = readFileSync(resolve('electron-builder.yml'), 'utf8');
    const verifier = readFileSync(resolve('scripts/verify-packaged-security.mjs'), 'utf8');

    expect(builder).toMatch(/^\s*grantFileProtocolExtraPrivileges:\s*true\s*$/m);
    expect(verifier).toMatch(
      /\[FuseV1Options\.GrantFileProtocolExtraPrivileges, FuseState\.ENABLE\]/,
    );
  });
});

describe('sanitizeBackendEnvironment', () => {
  it('keeps runtime configuration but strips unrelated host credentials', () => {
    const result = sanitizeBackendEnvironment({
      PATH: 'C:\\Windows',
      KNOWE_BROWSER_ENABLED: '1',
      DEEPSEEK_API_KEY: 'provider-key',
      GITHUB_TOKEN: 'github-secret',
      NPM_TOKEN: 'npm-secret',
      AWS_SECRET_ACCESS_KEY: 'aws-secret',
      SSH_AUTH_SOCK: 'agent-pipe',
    });
    expect(result).toEqual({
      PATH: 'C:\\Windows',
      KNOWE_BROWSER_ENABLED: '1',
      DEEPSEEK_API_KEY: 'provider-key',
    });
  });
});

describe('isTrustedTopLevelRuntimeFrame', () => {
  it('accepts only the exact top frame of an app-owned document', () => {
    const mainFrame = { processId: 17, routingId: 23 };
    expect(isTrustedTopLevelRuntimeFrame(mainFrame, mainFrame, true)).toBe(true);
    expect(isTrustedTopLevelRuntimeFrame({ processId: 17, routingId: 23 }, mainFrame, true)).toBe(true);
    expect(isTrustedTopLevelRuntimeFrame({ processId: 17, routingId: 24 }, mainFrame, true)).toBe(false);
    expect(isTrustedTopLevelRuntimeFrame(null, mainFrame, true)).toBe(false);
    expect(isTrustedTopLevelRuntimeFrame(undefined, mainFrame, true)).toBe(false);
    expect(isTrustedTopLevelRuntimeFrame(mainFrame, mainFrame, false)).toBe(false);
  });
});

describe('isAuthorizedRuntimeWebSocket', () => {
  const token = 'ab'.repeat(32);
  const mainFrame = { processId: 17, routingId: 23 };
  const base = {
    requestUrl: `ws://127.0.0.1:8081/?token=${token}`,
    expectedWsUrl: 'ws://127.0.0.1:8081',
    resourceType: 'webSocket',
    method: 'GET',
    requestWebContentsId: 7,
    mainWebContentsId: 7,
    requestFrame: mainFrame,
    mainFrame,
    appOwnPage: true,
    runtimeToken: token,
  };

  it('accepts an exact main-window upgrade and the missing-frame Chromium variant', () => {
    expect(isAuthorizedRuntimeWebSocket(base)).toBe(true);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestFrame: undefined })).toBe(true);
  });

  it('rejects child frames, other windows, endpoints, and bearer variations', () => {
    expect(isAuthorizedRuntimeWebSocket({
      ...base,
      requestFrame: { processId: 17, routingId: 99 },
    })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestWebContentsId: 8 })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestWebContentsId: undefined })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, appOwnPage: false })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, resourceType: 'xhr' })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, method: 'POST' })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestUrl: `ws://127.0.0.1:8082/?token=${token}` })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestUrl: `ws://127.0.0.1:8081/other?token=${token}` })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestUrl: base.requestUrl.replace('ws:', 'http:') })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestUrl: `${base.requestUrl}&extra=1` })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestUrl: `${base.requestUrl}&token=${token}` })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestUrl: base.requestUrl.replace('127.0.0.1', 'user@127.0.0.1') })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestUrl: `${base.requestUrl}#fragment` })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, requestUrl: `${base.requestUrl.slice(0, -1)}0` })).toBe(false);
    expect(isAuthorizedRuntimeWebSocket({ ...base, runtimeToken: '' })).toBe(false);
  });
});

describe('runtime bearer lifecycle and headers', () => {
  it('strips any casing of a renderer-supplied runtime token header', () => {
    expect(stripRuntimeTokenHeader({
      Accept: '*/*',
      'X-Knowe-Runtime-Token': 'one',
      'x-knowe-runtime-token': 'two',
    })).toEqual({ Accept: '*/*' });
  });

  it('keeps one token for the Electron process and exposes it only to the main frame', () => {
    const main = readFileSync(resolve('electron/main.ts'), 'utf8');
    expect(main.match(/runtimeToken = ''/g)).toHaveLength(1);
    expect(main).toMatch(/if \(!runtimeToken\) runtimeToken = randomBytes\(32\)\.toString\('hex'\)/);
    expect(main).toMatch(/ipcMain\.handle\(IPC\.getToken,[\s\S]*?requireMainSender\(evt\)/);
    expect(main).toMatch(/const wsFilter = `\$\{RUNTIME_ENDPOINTS\.wsUrl/);
    const restartHandler = main.match(/ipcMain\.handle\(IPC\.restart,[\s\S]*?return snapshot\(\);\s*\}\);/)?.[0];
    expect(restartHandler).toBeDefined();
    expect(restartHandler).toMatch(/await killBackend\(\);\s*spawnBackend\(\)/);
    expect(restartHandler).not.toMatch(/ensurePorts\(\)/);
  });
});

describe('configureRemoteDebugging', () => {
  it('never opens a debugging port in a packaged build', () => {
    const appendSwitch = vi.fn();
    const removeSwitch = vi.fn();
    expect(configureRemoteDebugging(
      { appendSwitch, removeSwitch },
      { isPackaged: true, env: { KNOWE_ENABLE_REMOTE_DEBUG: '1', KNOWE_REMOTE_DEBUG_PORT: '9222' } },
    )).toBe(false);
    expect(appendSwitch).not.toHaveBeenCalled();
    expect(removeSwitch.mock.calls).toEqual([
      ['remote-debugging-address'],
      ['remote-debugging-port'],
      ['remote-debugging-pipe'],
      ['remote-allow-origins'],
    ]);
  });

  it('stays disabled in development unless explicitly opted in', () => {
    const appendSwitch = vi.fn();
    const removeSwitch = vi.fn();
    expect(configureRemoteDebugging({ appendSwitch, removeSwitch }, { isPackaged: false, env: {} })).toBe(false);
    expect(appendSwitch).not.toHaveBeenCalled();
    expect(removeSwitch).toHaveBeenCalledWith('remote-debugging-port');
  });

  it('binds explicit development debugging to loopback with a narrow origin', () => {
    const appendSwitch = vi.fn();
    const removeSwitch = vi.fn();
    expect(configureRemoteDebugging(
      { appendSwitch, removeSwitch },
      { isPackaged: false, env: { KNOWE_ENABLE_REMOTE_DEBUG: '1', KNOWE_REMOTE_DEBUG_PORT: '9333' } },
    )).toBe(true);
    expect(appendSwitch.mock.calls).toEqual([
      ['remote-debugging-address', '127.0.0.1'],
      ['remote-debugging-port', '9333'],
      ['remote-allow-origins', 'devtools://devtools'],
    ]);
  });

  it('uses an ephemeral port when no safe port is supplied', () => {
    const appendSwitch = vi.fn();
    const removeSwitch = vi.fn();
    configureRemoteDebugging(
      { appendSwitch, removeSwitch },
      { isPackaged: false, env: { KNOWE_ENABLE_REMOTE_DEBUG: '1', KNOWE_REMOTE_DEBUG_PORT: '80' } },
    );
    expect(appendSwitch).toHaveBeenCalledWith('remote-debugging-port', '0');
  });
});
