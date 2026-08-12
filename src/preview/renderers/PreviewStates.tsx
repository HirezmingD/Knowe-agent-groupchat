/** 各格式渲染器共用的加载、失败状态与竞态安全异步 Hook。 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { IconRetry } from '../icons';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';

export const PreviewLoading: React.FC<{ label?: string }> = ({ label }) => {
  const { t } = useTranslation();
  return (
    <div className="pv-state" role="status" aria-live="polite" aria-busy="true">
      <span className="pv-spinner" aria-hidden="true" />
      <span className="pv-state-text">{label || t('preview.states.01') + '…'}</span>
    </div>
  );
};

export const PreviewError: React.FC<{ message: string; onRetry?: () => void }> = ({
  message,
  onRetry,
}) => {
  const { t } = useTranslation();
  return (
    <div className="pv-state pv-state-error" role="alert">
      <span className="pv-state-text">{message}</span>
      {onRetry && (
        <button type="button" className="pv-retry" onClick={onRetry}>
          <IconRetry size={15} />
          <span>{t('common.03')}</span>
        </button>
      )}
    </div>
  );
};

type AsyncStatus = 'loading' | 'ready' | 'error';

interface AsyncPreviewState<T> {
  status: AsyncStatus;
  data: T | null;
  error: string;
  reload: () => void;
}

/**
 * 每次依赖或重试令牌变化都启动新任务；旧任务即使稍后完成，也不会覆盖当前文件。
 * loader 应自行释放它创建但最终未交付给组件的临时资源。
 */
export function useAsyncPreview<T>(
  loader: () => Promise<T>,
  deps: React.DependencyList,
): AsyncPreviewState<T> {
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [nonce, setNonce] = useState(0);
  const runIdRef = useRef(0);

  useEffect(() => {
    const runId = ++runIdRef.current;
    let alive = true;
    setStatus('loading');
    setError('');
    setData(null);

    void loader()
      .then((result) => {
        if (!alive || runId !== runIdRef.current) return;
        setData(result);
        setStatus('ready');
      })
      .catch((reason: unknown) => {
        if (!alive || runId !== runIdRef.current) return;
        setError(reason instanceof Error ? reason.message : i18n.t('preview.states.02') + '。');
        setStatus('error');
      });

    return () => {
      alive = false;
    };
    // loader 由调用方配合 deps 控制；直接加入会让匿名函数每次渲染都重跑。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return { status, data, error, reload };
}
