import { describe, expect, it } from 'vitest';
import { newProjectRequestId, projectIdForCard } from './platform';

const PROJECT_ID = /^project_\d{14}$/;

describe('v0.18 project identity allocation', () => {
  it('uses canonical ids for both creation paths', () => {
    expect(newProjectRequestId()).toMatch(PROJECT_ID);
    expect(projectIdForCard('ap_frontend_regression')).toMatch(PROJECT_ID);
    expect(projectIdForCard('ap_frontend_regression')).not.toMatch(/^p_/);
  });

  it('keeps one stable id per approval card', () => {
    const first = projectIdForCard('ap_stable_card');
    const second = projectIdForCard('ap_stable_card');
    expect(second).toBe(first);
  });

  it('shares one monotonic allocator with the plus-button flow', () => {
    const manual = newProjectRequestId(1_700_000_000_000);
    const approval = projectIdForCard('ap_shared_allocator');
    expect(approval).not.toBe(manual);
  });
});
