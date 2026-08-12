/** 不支持格式的降级卡：展示身份，并通过后端校验后在文件管理器中定位。 */

import React, { useEffect, useRef, useState } from 'react';
import type { PreviewFilePayload } from '../../shared/bridge';
import { revealFileInFolder } from '../../store/filePreview';
import { FolderRevealIcon, IconForKind } from '../icons';
import { humanBytes, kindOf, typeLabel } from '../fileKinds';
import { useTranslation } from 'react-i18next';

function formatTime(value: string | undefined): string {
  if (!value) return '';
  const time = Date.parse(value);
  if (Number.isNaN(time)) return '';
  return new Date(time).toLocaleString();
}

const FallbackPreview: React.FC<{
  file: PreviewFilePayload;
  projectId: string;
  reason?: string;
}> = ({ file, projectId, reason }) => {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mountedRef = useRef(true);
  const runRef = useRef(0);
  const size = humanBytes(file.bytes);
  const time = formatTime(file.mtime);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      runRef.current += 1;
    };
  }, []);

  const reveal = (): void => {
    if (busy) return;
    const run = ++runRef.current;
    setBusy(true);
    setError('');
    void revealFileInFolder(projectId, file)
      .catch((cause: unknown) => {
        if (!mountedRef.current || run !== runRef.current) return;
        setError(cause instanceof Error ? cause.message : t('file.card.02'));
      })
      .finally(() => {
        if (!mountedRef.current || run !== runRef.current) return;
        setBusy(false);
      });
  };

  return (
    <div className="pv-fallback">
      <div className="pv-fallback-icon" aria-hidden="true">
        <IconForKind kind={kindOf(file)} size={40} />
      </div>
      <div className="pv-fallback-name">{file.name}</div>
      <div className="pv-fallback-reason">{reason || t('fallback.preview.04')}</div>
      <dl className="pv-fallback-facts">
        <div><dt>{t('contacts.view.27')}</dt><dd>{typeLabel(file)}</dd></div>
        {size && <div><dt>{t('fallback.preview.02')}</dt><dd>{size}</dd></div>}
        {time && <div><dt>{t('fallback.preview.01')}</dt><dd>{time}</dd></div>}
        <div><dt>{t('fallback.preview.03')}</dt><dd className="pv-path">{file.path}</dd></div>
      </dl>
      <button type="button" className="pv-open-ext solid" onClick={reveal} disabled={busy}>
        <FolderRevealIcon size={16} />
        <span>{busy ? t('preview.app.02') : t('preview.app.01')}</span>
      </button>
      {error && <div className="pv-inline-error" role="alert">{error}</div>}
    </div>
  );
};

export default FallbackPreview;
