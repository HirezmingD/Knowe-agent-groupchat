/** HTML preview via srcdoc — no URLs, no navigation events, no DNS. */

import React, { useEffect, useState } from 'react';
import type { PreviewFilePayload } from '../../shared/bridge';
import { fetchPreviewHtml } from '../../store/filePreview';
import { useTranslation } from 'react-i18next';

type LoadState = 'fetching' | 'ready' | 'error';

const HtmlPreview: React.FC<{ file: PreviewFilePayload; projectId: string }> = ({
  file,
  projectId,
}) => {
  const { t } = useTranslation();
  const [htmlContent, setHtmlContent] = useState('');
  const [state, setState] = useState<LoadState>('fetching');
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setHtmlContent('');
    setError('');
    setState('fetching');
    fetchPreviewHtml(projectId, file)
      .then((html) => {
        if (cancelled) return;
        setHtmlContent(html);
        setState('ready');
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : t('html.preview.02'));
        setState('error');
      });
    return () => { cancelled = true; };
  }, [file.path, projectId, (file as { resolved_project_id?: string }).resolved_project_id, retry]);

  return (
    <div className="pv-html">
      <div className="pv-security-note" role="note">
        {t('html.preview.sandbox1')}
        {t('html.preview.sandbox2')}
      </div>
      {state === 'fetching' ? (
        <div className="pv-state" role="status">{t('html.preview.03')}</div>
      ) : state === 'error' ? (
        <div className="pv-state pv-error" role="alert">
          <div>{error || t('html.preview.01')}</div>
          <button type="button" onClick={() => setRetry((v) => v + 1)}>{t('common.03')}</button>
        </div>
      ) : (
        <iframe
          className="pv-html-frame"
          title={file.name}
          srcDoc={htmlContent}
          sandbox="allow-scripts allow-forms allow-modals allow-popups allow-pointer-lock"
          referrerPolicy="no-referrer"
        />
      )}
    </div>
  );
};

export default HtmlPreview;
