/**
 * platform.ts — 平台会话与新建项目共用的 canonical project id 分配器。
 *
 * v0.18 的原则很简单：前端乐观会话从第一刻起就使用最终形状的
 * `project_YYYYMMDDHHMMSS`，不再先创建 `p_ap_*` 临时会话再等待异步重键。
 */

const CANONICAL_PROJECT_ID_RE = /^project_\d{14}$/;
const LAST_SECOND_STORAGE_KEY = 'knowe.project-id.last-second.v1';
const CARD_ID_STORAGE_PREFIX = 'knowe.project-id.card.v1:';

let lastProjectSecond = 0;
const cardProjectIds = new Map<string, string>();

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' && window.localStorage
      ? window.localStorage
      : null;
  } catch {
    // localStorage may be disabled (privacy mode / hardened WebView). In that case the
    // in-memory monotonic allocator still keeps one running renderer collision-free.
    return null;
  }
}

function readStoredSecond(): number {
  const raw = storage()?.getItem(LAST_SECOND_STORAGE_KEY);
  if (!raw || !/^\d+$/.test(raw)) return 0;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 0;
}

function writeStoredSecond(second: number): void {
  try {
    storage()?.setItem(LAST_SECOND_STORAGE_KEY, String(second));
  } catch {
    // Persistence is a collision-reduction aid, not a prerequisite for creating a project.
  }
}

function formatProjectSecond(second: number): string {
  const d = new Date(second * 1000);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `project_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`
    + `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

/**
 * Allocate a protocol-shaped project id.
 *
 * The monotonic second is shared by both “+” creation and approval-card creation, and is persisted
 * across renderer reloads when localStorage is available. This removes the old cross-component
 * collision window where each component kept a separate second counter.
 */
export function newProjectRequestId(nowMs: number = Date.now()): string {
  const wallSecond = Math.floor(nowMs / 1000);
  const nextSecond = Math.max(wallSecond, lastProjectSecond + 1, readStoredSecond() + 1);
  lastProjectSecond = nextSecond;
  writeStoredSecond(nextSecond);
  return formatProjectSecond(nextSecond);
}

/**
 * Return one stable canonical id for a create-project approval card.
 *
 * The card mapping is persisted so a renderer reload between `create_project` and `approve` retries
 * the same id instead of creating another project. Old `p_ap_*` clients remain supported by the
 * backend alias resolver; v0.18 clients no longer create that temporary identity at all.
 */
export function projectIdForCard(cardId: string): string {
  const key = cardId.trim();
  if (!key) return newProjectRequestId();

  const cached = cardProjectIds.get(key);
  if (cached) return cached;

  const store = storage();
  const storageKey = CARD_ID_STORAGE_PREFIX + key;
  const persisted = store?.getItem(storageKey);
  if (persisted && CANONICAL_PROJECT_ID_RE.test(persisted)) {
    cardProjectIds.set(key, persisted);
    return persisted;
  }

  const allocated = newProjectRequestId();
  cardProjectIds.set(key, allocated);
  try {
    store?.setItem(storageKey, allocated);
  } catch {
    // The module cache still guarantees stability for this renderer lifetime.
  }
  return allocated;
}
