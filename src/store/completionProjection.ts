/** [v1.0.13][R3] Version/authority ordering for replay-safe completion projections. */

export type CompletionAuthority = 'completion_status' | 'message' | 'completion_view_v1';

const AUTHORITY_RANK: Readonly<Record<CompletionAuthority, number>> = {
  completion_status: 1,
  message: 2,
  completion_view_v1: 3,
};

export interface CompletionProjectionClock {
  version: number;
  authority: CompletionAuthority;
}

export function normalizeCompletionVersion(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0;
}

/**
 * Decide whether an incoming projection may replace the visible text.
 * Version wins first; authority only breaks ties. Arrival order is irrelevant.
 */
export function shouldReplaceCompletionProjection(
  current: CompletionProjectionClock | undefined,
  incoming: CompletionProjectionClock,
): boolean {
  if (!current) return true;
  const currentVersion = normalizeCompletionVersion(current.version);
  const incomingVersion = normalizeCompletionVersion(incoming.version);
  if (incomingVersion !== currentVersion) return incomingVersion > currentVersion;
  return AUTHORITY_RANK[incoming.authority] > AUTHORITY_RANK[current.authority];
}

/** Accept metadata only when it is not from an older completion version. */
export function shouldAcceptCompletionMetadata(
  currentVersion: number | undefined,
  incomingVersion: number,
): boolean {
  return normalizeCompletionVersion(incomingVersion) >= normalizeCompletionVersion(currentVersion);
}

export function completionAuthorityRank(authority: CompletionAuthority | undefined): number {
  return authority ? AUTHORITY_RANK[authority] : 0;
}
