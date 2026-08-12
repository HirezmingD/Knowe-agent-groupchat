/**
 * projectAlias.ts — legacy optimistic request → canonical project identity reconciliation.
 *
 * v0.18 clients create approval-card conversations directly under a canonical project_* id.
 * This module remains as an upgrade bridge for v0.16/v0.17 clients and stale p_ap_* / slug retries:
 * a request alias is never a second project and must be atomically folded into the server id.
 */

import type { Conv, Item, Member } from './state';

function mergeProjectItems(optimistic: Item[], canonical: Item[]): Item[] {
  const merged = optimistic.slice();
  const userIndex = new Map<string, number>();
  const approvalIndex = new Map<string, number>();

  merged.forEach((item, index) => {
    if (item.kind === 'user' && item.cmid) userIndex.set(item.cmid, index);
    if (item.kind === 'approval') approvalIndex.set(item.cardId, index);
  });

  for (const item of canonical) {
    if (item.kind === 'user' && item.cmid && userIndex.has(item.cmid)) {
      merged[userIndex.get(item.cmid) as number] = item;
      continue;
    }
    if (item.kind === 'approval' && approvalIndex.has(item.cardId)) {
      merged[approvalIndex.get(item.cardId) as number] = item;
      continue;
    }
    merged.push(item);
  }
  return merged;
}

function mergeProjectMembers(optimistic: Member[], canonical: Member[]): Member[] {
  const byId = new Map<string, Member>();
  for (const member of optimistic) byId.set(member.id, member);
  for (const member of canonical) byId.set(member.id, member);
  return Array.from(byId.values());
}

/**
 * Atomically replace a temporary request key with the canonical project key.
 *
 * The merge is deliberately lossless: draft text, an ultra-fast optimistic user bubble, selected
 * directory, roster and unread state survive the re-key.  Canonical server state wins when the
 * same message/member already exists on both sides.
 */
export function reconcileProjectAlias(
  draft: {
    convs: Record<string, Conv>;
    projectOrder: string[];
    activeProjectId: string | null;
  },
  requestProjectId: string,
  canonicalProjectId: string,
): void {
  if (!requestProjectId || requestProjectId === canonicalProjectId) return;

  const optimistic = draft.convs[requestProjectId];
  const canonical = draft.convs[canonicalProjectId];

  if (optimistic) {
    for (const item of optimistic.items || []) {
      if (item.kind === 'approval' && item.projectId === requestProjectId) {
        item.projectId = canonicalProjectId;
      }
    }
    if (canonical) {
      canonical.items = mergeProjectItems(optimistic.items || [], canonical.items || []);
      canonical.members = mergeProjectMembers(optimistic.members || [], canonical.members || []);
      canonical.banner = canonical.banner ?? optimistic.banner;
      canonical.draft = canonical.draft || optimistic.draft || '';
      canonical.unread = Math.max(canonical.unread || 0, optimistic.unread || 0);
      canonical.projectDir = canonical.projectDir || optimistic.projectDir;
      canonical.projectName = canonical.projectName || optimistic.projectName;
    } else {
      optimistic.projectId = canonicalProjectId;
      draft.convs[canonicalProjectId] = optimistic;
    }
    delete draft.convs[requestProjectId];
  }

  const requestIndex = draft.projectOrder.indexOf(requestProjectId);
  const canonicalIndex = draft.projectOrder.indexOf(canonicalProjectId);
  const existingIndices = [requestIndex, canonicalIndex].filter((index) => index >= 0);
  const targetIndex = existingIndices.length ? Math.min(...existingIndices) : -1;
  for (let i = draft.projectOrder.length - 1; i >= 0; i -= 1) {
    if (draft.projectOrder[i] === requestProjectId
        || draft.projectOrder[i] === canonicalProjectId) {
      draft.projectOrder.splice(i, 1);
    }
  }
  if (targetIndex >= 0) {
    draft.projectOrder.splice(Math.min(targetIndex, draft.projectOrder.length), 0, canonicalProjectId);
  }

  if (draft.activeProjectId === requestProjectId) {
    draft.activeProjectId = canonicalProjectId;
  }
}
