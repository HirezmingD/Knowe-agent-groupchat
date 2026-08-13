/** Security-sensitive Electron command-line policy.
 *
 * Remote debugging exposes the renderer DOM, network traffic, preload bridge and
 * runtime bearer token to every local process that can reach the debugging port.
 * It is therefore forbidden in packaged builds and opt-in only while developing.
 */

import { timingSafeEqual } from 'node:crypto';

export type CommandLineSwitches = Pick<Electron.CommandLine, 'appendSwitch' | 'removeSwitch'>;

const REMOTE_DEBUG_SWITCHES = [
  'remote-debugging-address',
  'remote-debugging-port',
  'remote-debugging-pipe',
  'remote-allow-origins',
] as const;

const BACKEND_ENV_NAMES = new Set([
  'ALL_PROXY', 'APPDATA', 'COMSPEC', 'HOMEDRIVE', 'HOMEPATH', 'HTTP_PROXY',
  'HTTPS_PROXY', 'LANG', 'LANGUAGE', 'LC_ALL', 'LOCALAPPDATA', 'NO_PROXY',
  'NUMBER_OF_PROCESSORS', 'OS', 'PATH', 'PATHEXT', 'PROCESSOR_ARCHITECTURE',
  'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432',
  'REQUESTS_CA_BUNDLE', 'SSL_CERT_FILE', 'SYSTEMDRIVE', 'SYSTEMROOT', 'TEMP',
  'TMP', 'TZ', 'USERPROFILE', 'WINDIR',
]);

/**
 * Keep desktop/runtime configuration while dropping unrelated host credentials.
 * Provider credentials explicitly supported by Knowe retain their documented env
 * compatibility; GitHub/npm/cloud/SSH credentials never enter the backend process.
 */
export function sanitizeBackendEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const clean: NodeJS.ProcessEnv = {};
  for (const [name, value] of Object.entries(source)) {
    if (value === undefined) continue;
    const upper = name.toUpperCase();
    const allowed = BACKEND_ENV_NAMES.has(upper)
      || upper.startsWith('KNOWE_')
      || upper.startsWith('DEEPSEEK_')
      || upper.startsWith('PLAYWRIGHT_')
      || upper.startsWith('PYTHON');
    if (!allowed) continue;
    clean[name] = value;
  }
  return clean;
}

/**
 * Runtime credentials belong only to a trusted app document's top frame.
 * Project HTML shares the BrowserWindow webContents, so checking only its id
 * would accidentally grant the child frame the backend bearer token. Missing
 * or destroyed frame metadata deliberately fails closed.
 */
export function isTrustedTopLevelRuntimeFrame(
  requestFrame: unknown,
  mainFrame: unknown,
  appOwnPage: boolean,
): boolean {
  if (!appOwnPage || requestFrame === null || requestFrame === undefined
    || mainFrame === null || mainFrame === undefined) return false;
  if (requestFrame === mainFrame) return true;

  // webRequest may return a fresh WebFrameMain wrapper for the same Chromium
  // frame. Compare its stable process/routing identity instead of relying only
  // on JavaScript object identity.
  try {
    const request = requestFrame as { processId?: unknown; routingId?: unknown };
    const main = mainFrame as { processId?: unknown; routingId?: unknown };
    return typeof request.processId === 'number'
      && typeof request.routingId === 'number'
      && request.processId === main.processId
      && request.routingId === main.routingId;
  } catch {
    return false;
  }
}

function safeTokenEqual(left: string, right: string): boolean {
  const a = Buffer.from(left, 'utf8');
  const b = Buffer.from(right, 'utf8');
  return a.length === b.length && a.length > 0 && timingSafeEqual(a, b);
}

/**
 * Chromium sometimes omits `details.frame` for WebSocket upgrades. In that
 * case the URL bearer is the capability boundary: it is generated per process
 * and exposed by IPC only to the main top frame. Known child frames are still
 * rejected even if they somehow reproduce the URL.
 */
export function isAuthorizedRuntimeWebSocket(options: {
  requestUrl: string;
  expectedWsUrl: string;
  resourceType: string;
  method: string;
  requestWebContentsId?: number;
  mainWebContentsId?: number;
  requestFrame?: unknown;
  mainFrame?: unknown;
  appOwnPage: boolean;
  runtimeToken: string;
}): boolean {
  if (options.resourceType !== 'webSocket' || options.method !== 'GET'
    || typeof options.requestWebContentsId !== 'number'
    || options.requestWebContentsId !== options.mainWebContentsId
    || !options.appOwnPage
    || !/^[0-9a-f]{64}$/i.test(options.runtimeToken)) return false;

  if (options.requestFrame !== null && options.requestFrame !== undefined
    && !isTrustedTopLevelRuntimeFrame(options.requestFrame, options.mainFrame, true)) return false;

  try {
    const requested = new URL(options.requestUrl);
    const expected = new URL(options.expectedWsUrl);
    if (requested.protocol !== expected.protocol
      || requested.hostname !== expected.hostname
      || requested.port !== expected.port
      || requested.pathname !== expected.pathname
      || requested.username || requested.password || requested.hash) return false;

    const tokens = requested.searchParams.getAll('token');
    const keys = [...requested.searchParams.keys()];
    return tokens.length === 1
      && keys.length === 1
      && keys[0] === 'token'
      && safeTokenEqual(tokens[0] ?? '', options.runtimeToken);
  } catch {
    return false;
  }
}

/** Never let renderer-controlled headers become an alternate bearer channel. */
export function stripRuntimeTokenHeader(
  headers: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(headers).filter(([name]) => name.toLowerCase() !== 'x-knowe-runtime-token'),
  );
}

export function configureRemoteDebugging(
  commandLine: CommandLineSwitches,
  options: {
    isPackaged: boolean;
    env?: NodeJS.ProcessEnv;
  },
): boolean {
  const env = options.env ?? process.env;
  // Chromium also consumes switches supplied in Knowe.exe argv.  Returning
  // early is insufficient: a packaged app launched with
  // --remote-debugging-port would otherwise reopen CDP.  Remove every relevant
  // switch unless this exact development opt-in is active.
  if (options.isPackaged || env.KNOWE_ENABLE_REMOTE_DEBUG !== '1') {
    for (const name of REMOTE_DEBUG_SWITCHES) commandLine.removeSwitch(name);
    return false;
  }

  const requested = String(env.KNOWE_REMOTE_DEBUG_PORT ?? '').trim();
  const parsed = Number(requested);
  const port = requested && Number.isInteger(parsed) && parsed >= 1024 && parsed <= 65535
    ? String(parsed)
    : '0'; // Chromium selects an ephemeral port and writes DevToolsActivePort.

  commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');
  commandLine.appendSwitch('remote-debugging-port', port);
  commandLine.appendSwitch('remote-allow-origins', 'devtools://devtools');
  return true;
}
