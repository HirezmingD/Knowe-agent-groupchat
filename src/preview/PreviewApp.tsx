/**
 * 独立预览窗口根组件：标签状态只存在于本窗口，主应用和聊天 store 不参与生命周期。
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import type { PreviewFilePayload, PreviewOpenPayload } from '../shared/bridge';
import {
  comparablePreviewPath,
  relativePreviewFile,
  relativePreviewSourceKey,
  resolvePreviewFile,
  resolveProjectRelativeTarget,
  revealFileInFolder,
  type TrackableProducedFile,
} from '../store/filePreview';
import i18n from '../i18n';
import { invokeWindowControl } from '../shared/windowControl';
import PreviewTabs from './PreviewTabs';
import PreviewRenderer from './PreviewRenderer';
import { FolderRevealIcon, IconForKind, IconRetry } from './icons';
import { humanBytes, kindOf, typeLabel } from './fileKinds';

export interface PreviewTab {
  key: string;
  projectId: string;
  sourceFile: PreviewFilePayload;
  file: TrackableProducedFile;
  status: 'resolving' | 'ready' | 'error';
  error: string;
  mounted: boolean;
  token: number;
  /** null means no navigation request; empty string means reveal the top of the document. */
  fragment: string | null;
  fragmentRequest: number;
}

export interface PreviewTabsState {
  tabs: PreviewTab[];
  activeKey: string | null;
}

export type PreviewTabsAction =
  | { type: 'open'; payload: PreviewOpenPayload; token: number }
  | { type: 'resolved'; key: string; file: TrackableProducedFile; token: number }
  | { type: 'failed'; key: string; error: string; token: number }
  | { type: 'activate'; key: string; fragment?: string; fragmentRequest?: number }
  | { type: 'close'; key: string }
  | { type: 'move'; fromKey: string; toKey: string; side: 'before' | 'after' }
  | { type: 'mark-mounted'; key: string };

export const INITIAL_STATE: PreviewTabsState = { tabs: [], activeKey: null };

export function previewTabsReducer(
  state: PreviewTabsState,
  action: PreviewTabsAction,
): PreviewTabsState {
  switch (action.type) {
    case 'open': {
      const index = state.tabs.findIndex((tab) => tab.key === action.payload.sourceKey);
      const nextTab: PreviewTab = {
        key: action.payload.sourceKey,
        projectId: action.payload.projectId,
        sourceFile: { ...action.payload.file },
        file: { ...action.payload.file },
        status: 'resolving',
        error: '',
        mounted: false,
        token: action.token,
        fragment: action.payload.fragment ?? null,
        fragmentRequest: action.token,
      };
      if (index < 0) {
        return {
          tabs: [...state.tabs, nextTab],
          activeKey: nextTab.key,
        };
      }
      const tabs = state.tabs.slice();
      tabs[index] = nextTab;
      return { tabs, activeKey: nextTab.key };
    }
    case 'resolved': {
      const index = state.tabs.findIndex((tab) => tab.key === action.key);
      if (index < 0 || state.tabs[index]?.token !== action.token) return state;
      const tabs = state.tabs.slice();
      const current = tabs[index];
      if (!current) return state;
      tabs[index] = {
        ...current,
        file: action.file,
        status: 'ready',
        error: '',
      };
      return { ...state, tabs };
    }
    case 'failed': {
      const index = state.tabs.findIndex((tab) => tab.key === action.key);
      if (index < 0 || state.tabs[index]?.token !== action.token) return state;
      const tabs = state.tabs.slice();
      const current = tabs[index];
      if (!current) return state;
      tabs[index] = {
        ...current,
        status: 'error',
        error: action.error,
      };
      return { ...state, tabs };
    }
    case 'activate': {
      const index = state.tabs.findIndex((tab) => tab.key === action.key);
      if (index < 0) return state;
      if (action.fragment === undefined) return { ...state, activeKey: action.key };
      const tabs = state.tabs.slice();
      const current = tabs[index];
      if (!current) return state;
      tabs[index] = {
        ...current,
        fragment: action.fragment,
        fragmentRequest: action.fragmentRequest ?? current.fragmentRequest + 1,
      };
      return { ...state, tabs, activeKey: action.key };
    }
    case 'close': {
      const index = state.tabs.findIndex((tab) => tab.key === action.key);
      if (index < 0) return state;
      const tabs = state.tabs.filter((tab) => tab.key !== action.key);
      if (tabs.length === 0) return { tabs, activeKey: null };
      if (state.activeKey !== action.key) return { tabs, activeKey: state.activeKey };
      const right = state.tabs[index + 1];
      const left = state.tabs[index - 1];
      return { tabs, activeKey: right?.key ?? left?.key ?? tabs[0]?.key ?? null };
    }
    case 'move': {
      const fromIndex = state.tabs.findIndex((tab) => tab.key === action.fromKey);
      const originalTargetIndex = state.tabs.findIndex((tab) => tab.key === action.toKey);
      if (fromIndex < 0 || originalTargetIndex < 0 || fromIndex === originalTargetIndex) return state;
      const tabs = state.tabs.slice();
      const [moved] = tabs.splice(fromIndex, 1);
      if (!moved) return state;
      const targetIndex = tabs.findIndex((tab) => tab.key === action.toKey);
      if (targetIndex < 0) return state;
      tabs.splice(targetIndex + (action.side === 'after' ? 1 : 0), 0, moved);
      return { ...state, tabs };
    }
    case 'mark-mounted': {
      const index = state.tabs.findIndex((tab) => tab.key === action.key);
      const current = state.tabs[index];
      if (index < 0 || !current || current.mounted) return state;
      const tabs = state.tabs.slice();
      tabs[index] = { ...current, mounted: true };
      return { ...state, tabs };
    }
    default:
      return state;
  }
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : i18n.t('preview.app.06');
}

export { resolveProjectRelativeTarget } from '../store/filePreview';

const PreviewApp: React.FC = () => {
  const { t } = useTranslation();
  const [state, dispatch] = useReducer(previewTabsReducer, INITIAL_STATE);
  const stateRef = useRef(state);
  const tokenRef = useRef(0);
  const revealRunRef = useRef(0);
  const mountedRef = useRef(true);
  const [revealBusyKey, setRevealBusyKey] = useState<string | null>(null);
  const [revealError, setRevealError] = useState('');
  stateRef.current = state;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      revealRunRef.current += 1;
    };
  }, []);

  const resolveTab = useCallback(async (
    key: string,
    projectId: string,
    sourceFile: PreviewFilePayload,
    token: number,
  ): Promise<void> => {
    try {
      const file = await resolvePreviewFile(projectId, sourceFile);
      if (!mountedRef.current) return;
      dispatch({ type: 'resolved', key, file, token });
    } catch (reason) {
      if (!mountedRef.current) return;
      dispatch({ type: 'failed', key, error: errorMessage(reason), token });
    }
  }, []);

  const openTab = useCallback((payload: PreviewOpenPayload): void => {
    const token = ++tokenRef.current;
    dispatch({ type: 'open', payload, token });
    void resolveTab(payload.sourceKey, payload.projectId, payload.file, token);
  }, [resolveTab]);

  const openRelative = useCallback((fromTab: PreviewTab, href: string): void => {
    const target = resolveProjectRelativeTarget(fromTab.file.path, href);
    const targetPath = target.path;
    const existing = stateRef.current.tabs.find((candidate) => (
      candidate.projectId === fromTab.projectId
      && (
        comparablePreviewPath(candidate.file.path) === targetPath
        || comparablePreviewPath(candidate.sourceFile.path) === targetPath
      )
    ));
    if (existing) {
      dispatch({
        type: 'activate',
        key: existing.key,
        fragment: target.fragment,
        fragmentRequest: ++tokenRef.current,
      });
      return;
    }

    openTab({
      projectId: fromTab.projectId,
      sourceKey: relativePreviewSourceKey(fromTab.projectId, target.path),
      file: relativePreviewFile(target.path),
      fragment: target.fragment,
    });
  }, [openTab]);

  useEffect(() => {
    const bridge = window.knowe;
    if (!bridge?.onPreviewOpen || !bridge.previewReady) return undefined;
    const unsubscribe = bridge.onPreviewOpen(openTab);
    bridge.previewReady();
    return unsubscribe;
  }, [openTab]);

  const activateTab = useCallback((key: string): void => {
    dispatch({ type: 'activate', key });
  }, []);

  const moveTab = useCallback((
    fromKey: string,
    toKey: string,
    side: 'before' | 'after',
  ): void => {
    dispatch({ type: 'move', fromKey, toKey, side });
  }, []);

  const markMounted = useCallback((key: string): void => {
    dispatch({ type: 'mark-mounted', key });
  }, []);

  const closeTab = useCallback((key: string): void => {
    const isLast = stateRef.current.tabs.length === 1
      && stateRef.current.tabs[0]?.key === key;
    dispatch({ type: 'close', key });
    if (isLast) invokeWindowControl('close');
  }, []);

  const retryTab = useCallback((tab: PreviewTab): void => {
    const token = ++tokenRef.current;
    dispatch({
      type: 'open',
      payload: {
        projectId: tab.projectId,
        sourceKey: tab.key,
        file: tab.sourceFile,
        ...(tab.fragment !== null ? { fragment: tab.fragment } : {}),
      },
      token,
    });
    void resolveTab(tab.key, tab.projectId, tab.sourceFile, token);
  }, [resolveTab]);

  const cycleTab = useCallback((direction: 1 | -1): void => {
    const current = stateRef.current;
    if (current.tabs.length < 2 || !current.activeKey) return;
    const index = current.tabs.findIndex((tab) => tab.key === current.activeKey);
    const nextIndex = (index + direction + current.tabs.length) % current.tabs.length;
    const next = current.tabs[nextIndex];
    if (next) dispatch({ type: 'activate', key: next.key });
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.target instanceof HTMLIFrameElement) return;
      const command = event.metaKey || event.ctrlKey;
      if (command && event.key.toLowerCase() === 'w') {
        const active = stateRef.current.activeKey;
        if (!active) return;
        event.preventDefault();
        event.stopPropagation();
        closeTab(active);
        return;
      }
      if (event.ctrlKey && event.key === 'Tab') {
        event.preventDefault();
        event.stopPropagation();
        cycleTab(event.shiftKey ? -1 : 1);
      }
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [closeTab, cycleTab]);

  const activeTab = useMemo(
    () => state.tabs.find((tab) => tab.key === state.activeKey) ?? null,
    [state.activeKey, state.tabs],
  );

  useEffect(() => {
    document.title = activeTab
      ? `${activeTab.file.name} — ${t('preview.app.04')}`
      : t('preview.app.04');
  }, [activeTab]);

  useEffect(() => {
    setRevealError('');
  }, [state.activeKey]);

  const revealActive = useCallback((): void => {
    const tab = stateRef.current.tabs.find((candidate) => candidate.key === stateRef.current.activeKey);
    if (!tab || revealBusyKey) return;
    const run = ++revealRunRef.current;
    setRevealBusyKey(tab.key);
    setRevealError('');
    void revealFileInFolder(tab.projectId, tab.file)
      .catch((reason: unknown) => {
        if (
          mountedRef.current
          && run === revealRunRef.current
          && stateRef.current.activeKey === tab.key
        ) {
          setRevealError(errorMessage(reason));
        }
      })
      .finally(() => {
        if (mountedRef.current && run === revealRunRef.current) setRevealBusyKey(null);
      });
  }, [revealBusyKey]);

  return (
    <div className="preview-window-root">
      <header className="preview-window-head">
        <PreviewTabs
          tabs={state.tabs}
          activeKey={state.activeKey}
          onActivate={activateTab}
          onClose={closeTab}
          onMove={moveTab}
        />
        {activeTab && (
          <div className="preview-file-toolbar">
            <span className="preview-file-kind" aria-hidden="true">
              <IconForKind kind={kindOf(activeTab.file)} size={16} />
            </span>
            <span className="preview-file-format">{typeLabel(activeTab.file)}</span>
            {humanBytes(activeTab.file.bytes) && (
              <span className="preview-file-size">{humanBytes(activeTab.file.bytes)}</span>
            )}
            <span className="preview-file-path" title={activeTab.file.path}>
              {activeTab.file.path}
            </span>
            <button
              type="button"
              className="preview-reveal-button"
              onClick={revealActive}
              disabled={revealBusyKey === activeTab.key}
              aria-busy={revealBusyKey === activeTab.key}
              title={t('preview.app.01')}
            >
              <FolderRevealIcon size={15} />
              <span>{revealBusyKey === activeTab.key ? t('preview.app.02') + '…' : t('preview.app.07')}</span>
            </button>
          </div>
        )}
        {revealError && <div className="preview-toolbar-error" role="alert">{revealError}</div>}
      </header>

      <main className="preview-window-content">
        {state.tabs.length === 0 && (
          <div className="preview-window-empty" role="status">
            <span>{t('preview.app.05')}</span>
          </div>
        )}
        {state.tabs.map((tab) => {
          const active = tab.key === state.activeKey;
          return (
            <section
              key={tab.key}
              id={`preview-pane-${encodeURIComponent(tab.key)}`}
              role="tabpanel"
              aria-labelledby={`preview-tab-${encodeURIComponent(tab.key)}`}
              className="preview-pane"
              hidden={!active}
            >
              {tab.status === 'resolving' && (
                <div className="pv-state" role="status" aria-live="polite" aria-busy="true">
                  <span className="pv-spinner" aria-hidden="true" />
                  <span className="pv-state-text">{t('preview.app.03')}…</span>
                </div>
              )}
              {tab.status === 'error' && (
                <div className="pv-state pv-state-error" role="alert">
                  <span className="pv-state-text">{tab.error}</span>
                  <div className="pv-state-actions">
                    <button type="button" className="pv-retry" onClick={() => retryTab(tab)}>
                      <IconRetry size={15} />
                      <span>{t('common.03')}</span>
                    </button>
                    <button
                      type="button"
                      className="pv-open-ext"
                      onClick={revealActive}
                      disabled={revealBusyKey === tab.key}
                    >
                      <FolderRevealIcon size={15} />
                      <span>{revealBusyKey === tab.key ? t('preview.app.02') + '…' : t('preview.app.07')}</span>
                    </button>
                  </div>
                </div>
              )}
              {tab.status === 'ready' && (
                <PreviewRenderer
                  tabKey={tab.key}
                  file={tab.file}
                  projectId={tab.projectId}
                  fragment={tab.fragment}
                  fragmentRequest={tab.fragmentRequest}
                  onMounted={markMounted}
                  onOpenRelative={(href) => openRelative(tab, href)}
                />
              )}
            </section>
          );
        })}
      </main>
    </div>
  );
};

export default PreviewApp;
