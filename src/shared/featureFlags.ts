/** [v1.0.13][R1][R2][R3][R4] Renderer feature flags for staged rollout and rollback. */

export type KnoweFeatureFlag =
  | 'MODEL_READINESS_GATE_V1'
  | 'COMPLETION_VIEW_V1'
  | 'SEEN_SPEECH_V1'
  | 'IDENTITY_CONTRACT_V1';

const DEFAULTS: Readonly<Record<KnoweFeatureFlag, boolean>> = {
  MODEL_READINESS_GATE_V1: true,
  COMPLETION_VIEW_V1: true,
  SEEN_SPEECH_V1: true,
  IDENTITY_CONTRACT_V1: true,
};

const TRUE_VALUES = new Set(['1', 'true', 'yes', 'on', 'enabled']);
const FALSE_VALUES = new Set(['0', 'false', 'no', 'off', 'disabled']);

function parseFlag(raw: unknown, fallback: boolean): boolean {
  if (typeof raw === 'boolean') return raw;
  if (typeof raw !== 'string') return fallback;
  const value = raw.trim().toLowerCase();
  if (TRUE_VALUES.has(value)) return true;
  if (FALSE_VALUES.has(value)) return false;
  return fallback;
}

export function featureEnabled(flag: KnoweFeatureFlag): boolean {
  const fallback = DEFAULTS[flag];
  if (typeof window !== 'undefined') {
    try {
      const override = window.localStorage.getItem(`knowe.feature.${flag}`);
      if (override !== null) return parseFlag(override, fallback);
    } catch {
      // Sandboxed/private-mode storage can throw; continue with build-time/default value.
    }
  }
  const meta = import.meta as ImportMeta & {
    readonly env?: Readonly<Record<string, string | boolean | undefined>>;
  };
  return parseFlag(meta.env?.[`VITE_${flag}`], fallback);
}

export function featureFlagSnapshot(): Readonly<Record<KnoweFeatureFlag, boolean>> {
  return {
    MODEL_READINESS_GATE_V1: featureEnabled('MODEL_READINESS_GATE_V1'),
    COMPLETION_VIEW_V1: featureEnabled('COMPLETION_VIEW_V1'),
    SEEN_SPEECH_V1: featureEnabled('SEEN_SPEECH_V1'),
    IDENTITY_CONTRACT_V1: featureEnabled('IDENTITY_CONTRACT_V1'),
  };
}
