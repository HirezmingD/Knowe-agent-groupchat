/** [v1.0.13][R1][R2][R3][R4] Renderer feature-flag default/override tests. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { featureEnabled, featureFlagSnapshot } from './featureFlags';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('renderer feature flags', () => {
  it('defaults the complete v1.0.13 rollout set to enabled', () => {
    vi.stubGlobal('window', undefined);
    expect(Object.values(featureFlagSnapshot()).every(Boolean)).toBe(true);
  });

  it('accepts an explicit localStorage rollback override', () => {
    vi.stubGlobal('window', {
      localStorage: { getItem: (key: string) => key.endsWith('SEEN_SPEECH_V1') ? 'off' : null },
    });
    expect(featureEnabled('SEEN_SPEECH_V1')).toBe(false);
    expect(featureEnabled('IDENTITY_CONTRACT_V1')).toBe(true);
  });

  it('fails ambiguous override values back to the documented default', () => {
    vi.stubGlobal('window', {
      localStorage: { getItem: () => 'maybe' },
    });
    expect(featureEnabled('MODEL_READINESS_GATE_V1')).toBe(true);
  });
});
