/** [v1.0.13][R3] Permutation tests for order-independent completion projection. */
import { describe, expect, it } from 'vitest';
import { shouldReplaceCompletionProjection } from './completionProjection';

describe('completion projection authority', () => {
  it('never lets same-version status replace message content', () => {
    expect(shouldReplaceCompletionProjection(
      { version: 7, authority: 'message' },
      { version: 7, authority: 'completion_status' },
    )).toBe(false);
  });

  it('lets same-version message replace a status placeholder', () => {
    expect(shouldReplaceCompletionProjection(
      { version: 7, authority: 'completion_status' },
      { version: 7, authority: 'message' },
    )).toBe(true);
  });

  it('makes CompletionViewV1 authoritative within a version', () => {
    expect(shouldReplaceCompletionProjection(
      { version: 7, authority: 'message' },
      { version: 7, authority: 'completion_view_v1' },
    )).toBe(true);
    expect(shouldReplaceCompletionProjection(
      { version: 7, authority: 'completion_view_v1' },
      { version: 7, authority: 'message' },
    )).toBe(false);
  });

  it('lets a newer version replace any older authority', () => {
    expect(shouldReplaceCompletionProjection(
      { version: 7, authority: 'completion_view_v1' },
      { version: 8, authority: 'completion_status' },
    )).toBe(true);
  });

  it('ignores exact-authority replays within the same version', () => {
    expect(shouldReplaceCompletionProjection(
      { version: 7, authority: 'message' },
      { version: 7, authority: 'message' },
    )).toBe(false);
  });
});

describe('completion projection permutations', () => {
  type ProjectionEvent = { version?: number; authority: 'completion_status' | 'message' | 'completion_view_v1'; text: string };

  function reduce(events: ProjectionEvent[]): ProjectionEvent | undefined {
    let current: ProjectionEvent | undefined;
    for (const incoming of events) {
      if (shouldReplaceCompletionProjection(
        current ? { version: current.version ?? 0, authority: current.authority } : undefined,
        { version: incoming.version ?? 0, authority: incoming.authority },
      )) current = incoming;
    }
    return current;
  }

  function permutations<T>(items: T[]): T[][] {
    if (items.length <= 1) return [items];
    return items.flatMap((item, index) => permutations([
      ...items.slice(0, index), ...items.slice(index + 1),
    ]).map((tail) => [item, ...tail]));
  }

  it('converges to CompletionViewV1 for every duplicate/legacy order', () => {
    const events: ProjectionEvent[] = [
      { version: 4, authority: 'completion_status', text: '占位' },
      { version: 4, authority: 'message', text: '旧协议正文' },
      { version: 4, authority: 'completion_view_v1', text: '原子正文' },
      { version: 4, authority: 'completion_status', text: '占位重放' },
    ];
    for (const order of permutations(events)) {
      expect(reduce(order)?.text).toBe('原子正文');
    }
  });

  it('converges to legacy message when unversioned events carry different seq elsewhere', () => {
    const status = { authority: 'completion_status' as const, text: '占位' };
    const message = { authority: 'message' as const, text: '真实正文' };
    expect(reduce([message, status])?.text).toBe('真实正文');
    expect(reduce([status, message, status])?.text).toBe('真实正文');
  });
});
