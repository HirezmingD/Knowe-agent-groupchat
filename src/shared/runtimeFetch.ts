/**
 * [v1.0.24.5] HTTP 通道统一认证封装。
 *
 * 背景（架构报告 v1.0.24.5）：前端 HTTP 请求（10 处 fetch）原先全部依赖
 * Electron 主进程 webRequest 注入 X-Knowe-Runtime-Token——该注入在打包版
 * （file:// 渲染页面跨源请求，initiator/referrer 为空）判定失败 → 后端 401
 * runtime_auth_required。WS 通道早在 v1.0.18.4 就改为主动取 token 拼 URL
 * （socket.ts），本封装让 HTTP 通道与 WS 对齐：主动从主进程取 token 带 header。
 *
 * 设计要点：
 *   1. token 取一次缓存（与 socket.ts ensureAuthToken 同模式；每次启动新进程，
 *      后端 token 每进程一换，缓存跨请求安全）。
 *   2. 调用方自带 header 优先；token 必带（最后写入，不被覆盖）。
 *   3. 浏览器纯前端调试（window.knowe 不存在）→ 不加 token，行为同现状。
 *   4. 不吞错：fetch 失败原样抛出。
 */

let authTokenCache = '';
let authTokenReady = false;

/** [v1.0.24.5] 测试隔离用：清空已缓存的 token（生产路径不调用）。 */
export function __resetRuntimeAuthForTests(): void {
  authTokenCache = '';
  authTokenReady = false;
}

interface KnoweBridgeLike {
  getRuntimeToken?: () => Promise<string>;
}

function bridge(): KnoweBridgeLike | undefined {
  return (window as unknown as { knowe?: KnoweBridgeLike }).knowe;
}

async function ensureAuthToken(): Promise<string> {
  if (authTokenReady) return authTokenCache;
  try {
    const b = bridge();
    if (b?.getRuntimeToken) {
      authTokenCache = (await b.getRuntimeToken()) || '';
    }
  } catch {
    // 浏览器环境或 preload 未就绪：空 token（后端不强制时放行）
  }
  authTokenReady = true;
  return authTokenCache;
}

/**
 * HTTP 请求统一入口：自动携带 X-Knowe-Runtime-Token。
 * 调用方式与 fetch 完全一致；传参透传。
 */
export async function runtimeFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const token = await ensureAuthToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set('X-Knowe-Runtime-Token', token);
  return fetch(input, { ...init, headers });
}
