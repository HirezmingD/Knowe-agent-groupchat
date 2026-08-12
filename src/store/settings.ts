/**
 * [v1.0.13][R1] settings.ts — durable local settings plus acknowledged model apply state.
 *
 * Model testing and model application are separate transactions.  The first-run gate may
 * enter only when the opaque fingerprint returned by /settings/test is exactly the one
 * acknowledged by /settings.  Ordinary settings remain debounced; model activation has an
 * explicit Promise, validates HTTP/JSON boundaries and reconciles after backend reconnect.
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { featureEnabled } from '../shared/featureFlags';
import { runtimeHttpBase } from '../shared/runtimeEndpoints';
import { runtimeFetch } from '../shared/runtimeFetch';
import { cheapTierOf, migrateModelName, providerOf } from './modelCatalog';
import i18n, { normalizeLanguage } from '../i18n'; // [v1.0.21.3] 语言校验（i18n 不依赖 settings，无环）
import { DEFAULT_AVATAR_DATA_URL } from './defaultAvatar'; // [v1.0.23.5] 首次安装默认头像


export interface ModelBinding {
  provider: string;
  model: string;
  /** Transient user input. It is never returned by GET /settings or persisted in localStorage. */
  apiKey: string;
  /** Backend/public acknowledgement that a credential exists without exposing its value. */
  hasApiKey?: boolean;
  /** Explicit user intent; an empty apiKey alone always means "preserve the current key". */
  clearApiKey?: boolean;
  sealed: boolean;
}

export const APPROVAL_TIMEOUT_OPTIONS: { value: number; label: string }[] = [
  { value: 5, label: 'settings.06' },
  { value: 10, label: 'settings.02' },
  { value: 30, label: 'settings.04' },
  { value: 60, label: 'settings.01' },
  { value: 180, label: 'settings.03' },
  { value: 300, label: 'settings.05' },
  { value: 0, label: 'global.search.08' },
];

export function agentBindingKey(projectId: string, agentId: string): string {
  return `${projectId}::${agentId}`;
}

export type ModelApplyState = 'idle' | 'pending' | 'applied' | 'failed' | 'stale';

export interface ModelApplyResult {
  ok: boolean;
  restartRequired: boolean;
  settingsRevision: number;
  appliedFingerprint: string | null;
  zinniaCompatible: boolean;
  zinniaBindingSource: string;
  welcomeState: string;
  message: string;
}

export interface SettingsState {
  userName: string;
  avatarDataUrl: string | null;
  mainModel: ModelBinding | null;
  auxModel: ModelBinding | null;
  agentModels: Record<string, ModelBinding>;
  notifyDesktop: boolean;
  closeToTray: boolean;
  approvalTimeoutS: number;
  groupApprovalTimeouts: Record<string, number>;
  appearance: 'light' | 'dark';
  fontScale: 'small' | 'large';
  language: string; // [v1.0.21.3] 主要语言 'zh' | 'en'（界面 + 提示词语言）

  // Backend acknowledgement state is intentionally transient (not localStorage truth).
  modelApplyState: ModelApplyState;
  modelApplyError: string | null;
  settingsRevision: number;
  appliedFingerprint: string | null;
  zinniaCompatible: boolean;
  zinniaBindingSource: string;
  welcomeState: string;
  settingsConflict: string | null;
  restartRequired: boolean;

  setUserName: (name: string) => void;
  setAvatar: (dataUrl: string | null) => void;
  saveMainModel: (b: Omit<ModelBinding, 'sealed'>) => void;
  editMainModel: () => void;
  saveAuxModel: (b: Omit<ModelBinding, 'sealed'>) => void;
  editAuxModel: () => void;
  clearAuxModel: () => void;
  saveAgentModel: (projectId: string, agentId: string, b: Omit<ModelBinding, 'sealed'>) => void;
  editAgentModel: (projectId: string, agentId: string) => void;
  clearAgentModel: (projectId: string, agentId: string) => void;
  setNotifyDesktop: (on: boolean) => void;
  setCloseToTray: (on: boolean) => void;
  setApprovalTimeout: (s: number) => void;
  setGroupApprovalTimeout: (projectId: string, s: number) => void;
  setAppearance: (m: 'light' | 'dark') => void;
  setFontScale: (f: 'small' | 'large') => void;
  setLanguage: (lang: string) => void; // [v1.0.21.3] 仅本地 state；持久化走 pushToBackend
  markModelBindingStale: () => void;
  clearRestartRequired: () => void;
  applyModelBinding: (expectedFingerprint?: string) => Promise<ModelApplyResult>;
  reconcileFromBackend: () => Promise<ModelApplyResult>;
  pushToBackend: () => Promise<ModelApplyResult>;
}

export function effectiveAuxBinding(
  main: ModelBinding | null,
  aux: ModelBinding | null,
): (ModelBinding & { derived: boolean }) | null {
  if (aux && aux.sealed) return { ...aux, derived: false };
  if (main && main.sealed) {
    const cheap = cheapTierOf(main.provider) || main.model;
    return {
      provider: main.provider,
      model: cheap,
      apiKey: main.apiKey,
      hasApiKey: main.hasApiKey,
      clearApiKey: false,
      sealed: true,
      derived: true,
    };
  }
  return null;
}

export function effectiveAgentBinding(
  s: Pick<SettingsState, 'mainModel' | 'agentModels'>,
  projectId: string,
  agentId: string,
): ModelBinding | null {
  const own = s.agentModels[agentBindingKey(projectId, agentId)];
  if (own && own.sealed) return own;
  return s.mainModel && s.mainModel.sealed ? s.mainModel : null;
}

export function effectiveGroupTimeout(
  s: Pick<SettingsState, 'approvalTimeoutS' | 'groupApprovalTimeouts'>,
  projectId: string,
): number {
  const own = s.groupApprovalTimeouts[projectId];
  return typeof own === 'number' ? own : s.approvalTimeoutS;
}

type JsonObject = Record<string, unknown>;

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function credentialScope(
  providerId: string,
  baseUrl?: string,
  transport?: string,
): string {
  const provider = providerOf(providerId);
  const base = (baseUrl ?? provider?.baseUrl ?? '').trim().replace(/\/+$/, '').toLowerCase();
  const wire = (transport ?? provider?.transport ?? 'openai_chat').trim().toLowerCase();
  return `${providerId.trim().toLowerCase()}\u0000${base}\u0000${wire}`;
}

function sameCredentialScope(local: ModelBinding | null | undefined, raw: JsonObject): boolean {
  if (!local) return false;
  return credentialScope(local.provider) === credentialScope(
    stringValue(raw.provider),
    stringValue(raw.base_url),
    stringValue(raw.transport) || 'openai_chat',
  );
}

function bindingWire(b: ModelBinding | null | undefined): JsonObject | null {
  if (!b || !b.sealed || !b.provider || !b.model) return null;
  const provider = providerOf(b.provider);
  const wire: JsonObject = {
    provider: b.provider,
    model: b.model,
    base_url: provider?.baseUrl ?? '',
    transport: provider?.transport ?? 'openai_chat',
  };
  // A redacted binding deliberately sends no credential field at all.  The backend may preserve
  // its existing key only for the exact provider/base/transport scope.  Newly typed credentials
  // are transient and cross this boundary once, during test/apply.
  if (b.apiKey) wire.api_key = b.apiKey;
  if (b.clearApiKey === true) wire.clear_api_key = true;
  return wire;
}

function bindingFromBackend(
  value: unknown,
  local?: ModelBinding | null,
): ModelBinding | null {
  const raw = asObject(value);
  if (!raw) return null;
  const provider = stringValue(raw.provider).trim();
  // [v1.0.19.5] 上游目录同步后，旧模型名自动迁移到当前名（如 hy3-preview → hy3）。
  const model = migrateModelName(provider, stringValue(raw.model).trim());
  if (!provider || !model) return null;
  // Old backends may still return api_key. Treat only its presence as metadata and never copy
  // the secret into renderer state. A same-scope local edit may remain transient until POST ack.
  const backendHasKey = raw.has_api_key === true || Boolean(stringValue(raw.api_key));
  const transientKey = sameCredentialScope(local, raw) ? (local?.apiKey ?? '') : '';
  return {
    provider,
    model,
    apiKey: transientKey,
    hasApiKey: backendHasKey,
    clearApiKey: false,
    sealed: true,
  };
}

function bindingSignature(binding: ModelBinding | null): string {
  if (!binding?.sealed) return '';
  return [
    credentialScope(binding.provider),
    binding.model.trim(),
    binding.hasApiKey === true || Boolean(binding.apiKey) ? 'key' : 'no-key',
  ].join('\u0000');
}

function bindingHasCredential(binding: ModelBinding | null | undefined): boolean {
  return Boolean(binding && (binding.hasApiKey === true || binding.apiKey));
}

function acknowledgedBinding(
  binding: ModelBinding | null,
  fallback?: ModelBinding | null,
): ModelBinding | null {
  if (!binding) return null;
  const cleared = binding.clearApiKey === true;
  const inherited = Boolean(
    !cleared
    && fallback
    && credentialScope(binding.provider) === credentialScope(fallback.provider)
    && bindingHasCredential(fallback),
  );
  return {
    ...binding,
    apiKey: '',
    hasApiKey: cleared ? false : (bindingHasCredential(binding) || inherited),
    clearApiKey: false,
  };
}

function persistedBinding(binding: ModelBinding | null): ModelBinding | null {
  if (!binding) return null;
  return {
    ...binding,
    apiKey: '',
    // Only a backend acknowledgement is durable metadata.  A newly typed but not-yet-saved
    // secret must never turn into a false "saved key" claim after a renderer reload.
    hasApiKey: binding.hasApiKey === true,
    clearApiKey: false,
  };
}

function persistedBindings(bindings: Record<string, ModelBinding>): Record<string, ModelBinding> {
  return Object.fromEntries(
    Object.entries(bindings).map(([key, binding]) => [key, persistedBinding(binding) as ModelBinding]),
  );
}

function sealedBinding(
  binding: Omit<ModelBinding, 'sealed'>,
  previous?: ModelBinding | null,
): ModelBinding {
  const clearApiKey = binding.clearApiKey === true;
  const sameScope = Boolean(
    previous
    && credentialScope(previous.provider) === credentialScope(binding.provider),
  );
  return {
    ...binding,
    apiKey: binding.apiKey || '',
    hasApiKey: clearApiKey
      ? false
      : (binding.hasApiKey === true || (sameScope && previous?.hasApiKey === true)),
    clearApiKey,
    sealed: true,
  };
}

function acknowledgePersistedSecrets(body: JsonObject): void {
  const current = useSettingsStore.getState();
  const effective = asObject(body.effective);
  const publicMain = bindingFromBackend(effective?.main_model);
  const acknowledgedMain = publicMain ?? acknowledgedBinding(current.mainModel);
  useSettingsStore.setState({
    mainModel: acknowledgedMain,
    // Backend apply permits an explicit auxiliary/Agent binding to reuse the newly persisted main
    // credential only inside the same provider/base/transport scope. Mirror that acknowledgement
    // without ever copying the credential value into renderer state or localStorage.
    auxModel: acknowledgedBinding(current.auxModel, acknowledgedMain),
    agentModels: Object.fromEntries(
      Object.entries(current.agentModels).map(([key, binding]) => [
        key,
        acknowledgedBinding(binding, acknowledgedMain) as ModelBinding,
      ]),
    ),
  });
}

function buildWirePayload(s: SettingsState): JsonObject {
  const agentModels: Record<string, unknown> = {};
  for (const [key, binding] of Object.entries(s.agentModels)) {
    const wire = bindingWire(binding);
    if (wire) agentModels[key] = wire;
  }
  return {
    user_name: s.userName,
    main_model: bindingWire(s.mainModel),
    aux_model: bindingWire(effectiveAuxBinding(s.mainModel, s.auxModel)),
    agent_models: agentModels,
    approval_timeout_s: s.approvalTimeoutS,
    group_approval_timeouts: { ...s.groupApprovalTimeouts },
    language: normalizeLanguage(s.language), // [v1.0.21.3]
  };
}

function errorMessage(body: JsonObject | null, status: number): string {
  const direct = body ? stringValue(body.message || body.error || body.detail).trim() : '';
  return direct || i18n.t('settings.view.applyFailed', { status });
}

function parseApplyResult(
  body: JsonObject,
  status: number,
  restartFallback = false,
): ModelApplyResult {
  const ok = body.ok === true;
  const restartRequired = typeof body.restart_required === 'boolean'
    ? body.restart_required
    : restartFallback;
  return {
    ok,
    restartRequired,
    settingsRevision: Math.max(0, Math.trunc(numberValue(body.settings_revision))),
    appliedFingerprint: stringValue(body.applied_fingerprint).trim() || null,
    zinniaCompatible: body.zinnia_compatible === true,
    zinniaBindingSource: stringValue(body.zinnia_binding_source).trim() || 'unknown',
    welcomeState: stringValue(body.welcome_state).trim() || 'unknown',
    message: ok
      ? (restartRequired
        ? i18n.t('settings.17')
        : i18n.t('settings.16'))
      : errorMessage(body, status),
  };
}

let pushTimer: ReturnType<typeof setTimeout> | null = null;
let postQueue: Promise<void> = Promise.resolve();

function cancelScheduledPush(): void {
  if (pushTimer) clearTimeout(pushTimer);
  pushTimer = null;
}

function schedulePush(): void {
  cancelScheduledPush();
  pushTimer = setTimeout(() => {
    pushTimer = null;
    void postCurrentSettings(undefined, true);
  }, 400);
}

function updateBackendAckMetadata(result: ModelApplyResult): void {
  const current = useSettingsStore.getState();
  useSettingsStore.setState({
    settingsRevision: result.settingsRevision,
    appliedFingerprint: result.appliedFingerprint,
    zinniaCompatible: result.zinniaCompatible,
    zinniaBindingSource: result.zinniaBindingSource,
    welcomeState: result.welcomeState,
    restartRequired: result.ok ? result.restartRequired : current.restartRequired,
  });
}

function updateApplyState(result: ModelApplyResult, expectedFingerprint?: string): void {
  const current = useSettingsStore.getState();
  const fingerprintMatches = !expectedFingerprint
    || result.appliedFingerprint === expectedFingerprint;
  const fullyReady = result.ok && fingerprintMatches && result.zinniaCompatible;
  const error = fullyReady
    ? null
    : (!result.ok
      ? result.message
      : (!fingerprintMatches
        ? i18n.t('settings.13')
        : i18n.t('settings.14')));
  useSettingsStore.setState({
    modelApplyState: fullyReady ? 'applied' : (result.ok ? 'stale' : 'failed'),
    modelApplyError: error,
    settingsRevision: result.settingsRevision,
    appliedFingerprint: result.appliedFingerprint,
    zinniaCompatible: result.zinniaCompatible,
    zinniaBindingSource: result.zinniaBindingSource,
    welcomeState: result.welcomeState,
    restartRequired: result.ok ? result.restartRequired : current.restartRequired,
  });
}

async function postCurrentSettingsNow(
  expectedFingerprint?: string,
  silent = false,
): Promise<ModelApplyResult> {
  const state = useSettingsStore.getState();
  const payload = buildWirePayload(state);
  if (expectedFingerprint) payload.expected_fingerprint = expectedFingerprint;
  if (state.settingsRevision > 0) payload.expected_revision = state.settingsRevision;

  try {
    const response = await runtimeFetch(`${runtimeHttpBase()}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = asObject(await response.json().catch(() => null));
    if (!response.ok || !body) {
      const failed: ModelApplyResult = {
        ok: false,
        restartRequired: state.restartRequired,
        settingsRevision: body
          ? Math.max(state.settingsRevision, Math.trunc(numberValue(body.settings_revision)))
          : state.settingsRevision,
        appliedFingerprint: body
          ? (stringValue(body.applied_fingerprint).trim() || state.appliedFingerprint)
          : state.appliedFingerprint,
        zinniaCompatible: false,
        zinniaBindingSource: 'unknown',
        welcomeState: state.welcomeState,
        message: errorMessage(body, response.status),
      };
      if (!silent) updateApplyState(failed, expectedFingerprint);
      return failed;
    }
    const result = parseApplyResult(body, response.status);
    const persisted = result.ok;
    if (persisted) acknowledgePersistedSecrets(body);
    if (expectedFingerprint && result.appliedFingerprint !== expectedFingerprint) {
      result.ok = false;
      result.message = i18n.t('settings.13');
    }
    if (silent) updateBackendAckMetadata(result);
    else updateApplyState(result, expectedFingerprint);
    return result;
  } catch {
    const failed: ModelApplyResult = {
      ok: false,
      restartRequired: state.restartRequired,
      settingsRevision: state.settingsRevision,
      appliedFingerprint: state.appliedFingerprint,
      zinniaCompatible: false,
      zinniaBindingSource: 'unknown',
      welcomeState: state.welcomeState,
      message: i18n.t('settings.12'),
    };
    if (!silent) updateApplyState(failed, expectedFingerprint);
    return failed;
  }
}

function postCurrentSettings(
  expectedFingerprint?: string,
  silent = false,
): Promise<ModelApplyResult> {
  if (!silent) {
    cancelScheduledPush();
    useSettingsStore.setState({ modelApplyState: 'pending', modelApplyError: null });
  }
  // A debounced persistence POST and an explicit test→apply transaction may be
  // initiated in the same tick. Serialize them so the explicit POST reads the
  // revision produced by the ordinary save and cannot be overwritten by it.
  const operation = postQueue.then(
    () => postCurrentSettingsNow(expectedFingerprint, silent),
    () => postCurrentSettingsNow(expectedFingerprint, silent),
  );
  postQueue = operation.then(() => undefined, () => undefined);
  return operation;
}

async function reconcileSettings(): Promise<ModelApplyResult> {
  const state = useSettingsStore.getState();
  try {
    const response = await runtimeFetch(`${runtimeHttpBase()}/settings`, { method: 'GET' });
    const body = asObject(await response.json().catch(() => null));
    if (!response.ok || !body) {
      return {
        ok: false,
        restartRequired: state.restartRequired,
        settingsRevision: state.settingsRevision,
        appliedFingerprint: state.appliedFingerprint,
        zinniaCompatible: false,
        zinniaBindingSource: 'unknown',
        welcomeState: state.welcomeState,
        message: errorMessage(body, response.status),
      };
    }

    const backendMain = bindingFromBackend(body.main_model, state.mainModel);
    const backendAux = bindingFromBackend(body.aux_model, state.auxModel);
    const backendAgentsRaw = asObject(body.agent_models);
    const backendAgents: Record<string, ModelBinding> = {};
    for (const [key, value] of Object.entries(backendAgentsRaw ?? {})) {
      const binding = bindingFromBackend(value, state.agentModels[key]);
      if (binding) backendAgents[key] = binding;
    }
    const revision = Math.max(0, Math.trunc(numberValue(body.settings_revision)));
    const conflict = Boolean(
      state.mainModel?.sealed
      && backendMain
      && bindingSignature(state.mainModel) !== bindingSignature(backendMain),
    );

    if (backendMain) {
      useSettingsStore.setState({
        mainModel: backendMain,
        auxModel: backendAux,
        agentModels: backendAgents,
        userName: stringValue(body.user_name).trim() || state.userName,
        approvalTimeoutS: numberValue(body.approval_timeout_s, state.approvalTimeoutS),
        groupApprovalTimeouts: Object.fromEntries(
          Object.entries(asObject(body.group_approval_timeouts) ?? {})
            .filter((entry): entry is [string, number] => typeof entry[1] === 'number'),
        ),
        settingsConflict: conflict
          ? i18n.t('settings.08')
          : null,
        language: normalizeLanguage(stringValue(body.language)), // [v1.0.21.3]
      });
    }

    const result = parseApplyResult(
      { ...body, ok: true, settings_revision: revision },
      response.status,
      state.restartRequired,
    );
    updateApplyState(result);

    // First install: local sealed settings exist while the backend has no binding.  Push
    // the snapshot for persistence, but activation remains stale until test→explicit apply.
    if (!backendMain && state.mainModel?.sealed && Boolean(state.mainModel.apiKey)) {
      return postCurrentSettings(undefined, true);
    }
    return result;
  } catch {
    return {
      ok: false,
      restartRequired: state.restartRequired,
      settingsRevision: state.settingsRevision,
      appliedFingerprint: state.appliedFingerprint,
      zinniaCompatible: false,
      zinniaBindingSource: 'unknown',
      welcomeState: state.welcomeState,
      message: i18n.t('settings.11'),
    };
  }
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      userName: i18n.t('settings.15'),
      avatarDataUrl: DEFAULT_AVATAR_DATA_URL, // [v1.0.23.5] 首次安装默认头像（用户当前设置的固化）
      mainModel: null,
      auxModel: null,
      agentModels: {},
      notifyDesktop: true,
      closeToTray: true,
      approvalTimeoutS: 300,
      groupApprovalTimeouts: {},
      appearance: 'light',
      fontScale: 'small',
      language: 'zh',

      modelApplyState: 'idle',
      modelApplyError: null,
      settingsRevision: 0,
      appliedFingerprint: null,
      zinniaCompatible: false,
      zinniaBindingSource: 'unknown',
      welcomeState: 'unknown',
      settingsConflict: null,
      restartRequired: false,

      setUserName(name): void {
        set({ userName: name });
        schedulePush();
      },
      setAvatar(dataUrl): void { set({ avatarDataUrl: dataUrl }); },
      saveMainModel(binding): void {
        const previous = get().mainModel;
        // [v1.0.19.5] 保存前迁移旧模型名（目录同步后旧名在目录里查不到）。
        const migrated = { ...binding, model: migrateModelName(binding.provider, binding.model) };
        set({
          mainModel: sealedBinding(migrated, previous),
          agentModels: {},
          modelApplyState: 'stale',
          modelApplyError: null,
          appliedFingerprint: null,
          zinniaCompatible: false,
          settingsConflict: null,
        });
        schedulePush();
      },
      editMainModel(): void {
        const current = get().mainModel;
        if (current) set({
          mainModel: { ...current, sealed: false },
          modelApplyState: 'stale',
          modelApplyError: null,
          appliedFingerprint: null,
          zinniaCompatible: false,
        });
      },
      saveAuxModel(binding): void {
        // [v1.0.19.5] 保存前迁移旧模型名。
        const migrated = { ...binding, model: migrateModelName(binding.provider, binding.model) };
        set({ auxModel: sealedBinding(migrated, get().auxModel), modelApplyState: 'stale' });
        schedulePush();
      },
      editAuxModel(): void {
        const current = get().auxModel;
        if (current) set({ auxModel: { ...current, sealed: false }, modelApplyState: 'stale' });
      },
      clearAuxModel(): void {
        set({ auxModel: null, modelApplyState: 'stale' });
        schedulePush();
      },
      saveAgentModel(projectId, agentId, binding): void {
        const key = agentBindingKey(projectId, agentId);
        // [v1.0.19.5] 保存前迁移旧模型名。
        const migrated = { ...binding, model: migrateModelName(binding.provider, binding.model) };
        set((current) => ({
          agentModels: {
            ...current.agentModels,
            [key]: sealedBinding(migrated, current.agentModels[key] ?? current.mainModel),
          },
        }));
        schedulePush();
      },
      editAgentModel(projectId, agentId): void {
        const key = agentBindingKey(projectId, agentId);
        set((current) => {
          const base = current.agentModels[key]
            ?? current.mainModel
            ?? {
              provider: '', model: '', apiKey: '', hasApiKey: false, clearApiKey: false, sealed: false,
            };
          return { agentModels: { ...current.agentModels, [key]: { ...base, sealed: false } } };
        });
      },
      clearAgentModel(projectId, agentId): void {
        const key = agentBindingKey(projectId, agentId);
        set((current) => {
          if (!(key in current.agentModels)) return current;
          const next = { ...current.agentModels };
          delete next[key];
          return { agentModels: next };
        });
        schedulePush();
      },
      setNotifyDesktop(on): void {
        set({ notifyDesktop: on });
        syncDesktopPrefs(get());
      },
      setCloseToTray(on): void {
        set({ closeToTray: on });
        syncDesktopPrefs(get());
      },
      setApprovalTimeout(seconds): void {
        set({ approvalTimeoutS: seconds, groupApprovalTimeouts: {} });
        schedulePush();
      },
      setGroupApprovalTimeout(projectId, seconds): void {
        set((current) => ({
          groupApprovalTimeouts: { ...current.groupApprovalTimeouts, [projectId]: seconds },
        }));
        schedulePush();
      },
      setAppearance(mode): void { set({ appearance: mode }); },
      setFontScale(scale): void { set({ fontScale: scale }); },
      setLanguage(lang): void {
        // [v1.0.21.3] 仅本地 state；持久化由调用方（PrimaryLanguageModule）显式 pushToBackend。
        const normalized = normalizeLanguage(lang);
        set({ language: normalized });
      },
      markModelBindingStale(): void {
        set({
          modelApplyState: 'stale',
          modelApplyError: null,
          appliedFingerprint: null,
          zinniaCompatible: false,
        });
      },
      clearRestartRequired(): void { set({ restartRequired: false }); },
      async applyModelBinding(expectedFingerprint): Promise<ModelApplyResult> {
        if (!featureEnabled('MODEL_READINESS_GATE_V1')) {
          return postCurrentSettings(undefined, false);
        }
        return postCurrentSettings(expectedFingerprint, false);
      },
      async reconcileFromBackend(): Promise<ModelApplyResult> {
        const result = await reconcileSettings();
        syncDesktopPrefs(get());
        return result;
      },
      async pushToBackend(): Promise<ModelApplyResult> {
        const result = await postCurrentSettings(undefined, false);
        syncDesktopPrefs(get());
        return result;
      },
    }),
    {
      name: 'knowe-settings-v1',
      storage: createJSONStorage(() => localStorage),
      version: 2,
      migrate: (persistedState) => {
        const raw = asObject(persistedState);
        if (!raw) return persistedState as SettingsState;
        const redactLegacyBinding = (value: unknown): unknown => {
          const binding = asObject(value);
          if (!binding) return value;
          return {
            ...binding,
            apiKey: '',
            hasApiKey: binding.hasApiKey === true || Boolean(stringValue(binding.apiKey)),
            clearApiKey: false,
          };
        };
        const agents = asObject(raw.agentModels);
        return {
          ...raw,
          mainModel: redactLegacyBinding(raw.mainModel),
          auxModel: redactLegacyBinding(raw.auxModel),
          agentModels: Object.fromEntries(
            Object.entries(agents ?? {}).map(([key, value]) => [key, redactLegacyBinding(value)]),
          ),
        } as unknown as SettingsState;
      },
      partialize: (state) => ({
        userName: state.userName,
        avatarDataUrl: state.avatarDataUrl,
        mainModel: persistedBinding(state.mainModel),
        auxModel: persistedBinding(state.auxModel),
        agentModels: persistedBindings(state.agentModels),
        notifyDesktop: state.notifyDesktop,
        closeToTray: state.closeToTray,
        approvalTimeoutS: state.approvalTimeoutS,
        groupApprovalTimeouts: state.groupApprovalTimeouts,
        appearance: state.appearance,
        fontScale: state.fontScale,
        language: normalizeLanguage(state.language), // [v1.0.21.3] localStorage 持久化
      }),
    },
  ),
);

interface DesktopPrefsBridge {
  setNotifyPrefs?: (prefs: { desktop: boolean; closeToTray: boolean }) => void;
}

function syncDesktopPrefs(s: SettingsState): void {
  if (typeof window === 'undefined') return;
  const value = window as unknown as { knowe?: DesktopPrefsBridge };
  value.knowe?.setNotifyPrefs?.({ desktop: s.notifyDesktop, closeToTray: s.closeToTray });
}

export interface TestResult {
  ok: boolean;
  message: string;
  latencyMs?: number;
  testedFingerprint?: string;
  zinniaCompatible?: boolean;
}

/**
 * [v1.0.24.1] 测试任意模型绑定（含草稿态：provider/model/apiKey 未封存也可测）。
 * 后端 /settings/test 接受 binding 整体，与 testModelConnection 同一条通道。
 */
export async function testBinding(
  b: { provider: string; model: string; apiKey: string },
): Promise<TestResult> {
  if (!b.provider || !b.model) {
    return { ok: false, message: i18n.t('settings.07') };
  }
  const wire: ModelBinding = {
    provider: b.provider,
    model: b.model,
    apiKey: b.apiKey.trim(),
    hasApiKey: Boolean(b.apiKey.trim()),
    clearApiKey: false,
    sealed: true,
  };
  try {
    const response = await runtimeFetch(`${runtimeHttpBase()}/settings/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target: 'main', binding: bindingWire(wire) }),
    });
    const body = asObject(await response.json().catch(() => null));
    if (!response.ok || !body) {
      return { ok: false, message: errorMessage(body, response.status) };
    }
    const fingerprint = stringValue(body.tested_fingerprint).trim();
    const ok = body.ok === true && (!featureEnabled('MODEL_READINESS_GATE_V1') || Boolean(fingerprint));
    return {
      ok,
      message: ok
        ? (stringValue(body.message).trim() || i18n.t('settings.19'))
        : (stringValue(body.message).trim() || i18n.t('settings.09')),
      latencyMs: typeof body.latency_ms === 'number' ? body.latency_ms : undefined,
      testedFingerprint: fingerprint || undefined,
      zinniaCompatible: body.zinnia_compatible === true,
    };
  } catch {
    return { ok: false, message: i18n.t('settings.10') };
  }
}

export async function testModelConnection(target: 'main' | 'aux'): Promise<TestResult> {
  const state = useSettingsStore.getState();
  const binding = target === 'main'
    ? state.mainModel
    : effectiveAuxBinding(state.mainModel, state.auxModel);
  if (!binding || !binding.sealed) {
    return {
      ok: false,
      message: target === 'main' ? i18n.t('settings.07') : i18n.t('settings.18'),
    };
  }
  return testBinding(binding);
}
