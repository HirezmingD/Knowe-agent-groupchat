/** 纯文本预览；CSV/TSV 也保持原始文本，不擅自解释为表格。 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import type { PreviewFilePayload } from '../../shared/bridge';
import { fetchPreviewText } from '../../store/filePreview';
import i18n from '../../i18n';
import { PreviewError, PreviewLoading, useAsyncPreview } from './PreviewStates';

const MAX_TEXT_CHARS = 2_000_000;
const MAX_TEXT_LINES = 20_000;

interface TextModel {
  text: string;
  totalLines: number;
  truncated: boolean;
}

export async function loadText(
  projectId: string,
  file: PreviewFilePayload,
): Promise<TextModel> {
  const raw = await fetchPreviewText(projectId, file);
  if (raw.includes('\u0000')) throw new Error(i18n.t('text.preview.02'));
  const lines = raw.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split('\n');
  const totalLines = lines.length;
  let text = lines.slice(0, MAX_TEXT_LINES).join('\n');
  let truncated = totalLines > MAX_TEXT_LINES;
  if (text.length > MAX_TEXT_CHARS) {
    text = text.slice(0, MAX_TEXT_CHARS);
    truncated = true;
  }
  return { text, totalLines, truncated };
}

const TextPreview: React.FC<{ file: PreviewFilePayload; projectId: string }> = ({
  file,
  projectId,
}) => {
  const { t } = useTranslation();
  const { status, data, error, reload } = useAsyncPreview(
    () => loadText(projectId, file),
    [projectId, file.path, file.file_id, file.mtime_ns],
  );

  if (status === 'loading') return <PreviewLoading label={t('text.preview.03') + '…'} />;
  if (status === 'error') return <PreviewError message={error} onRetry={reload} />;
  if (!data) return <PreviewError message={t('text.preview.01')} onRetry={reload} />;

  return (
    <div className="pv-text">
      <div className="pv-text-meta">{t('common.lineCount', { n: data.totalLines.toLocaleString() })}</div>
      <pre className="pv-text-body" tabIndex={0}>{data.text || '\u200b'}</pre>
      {data.truncated && (
        <div className="pv-text-truncated" role="note">
          {t('text.preview.truncated', { n: MAX_TEXT_LINES.toLocaleString(), m: MAX_TEXT_CHARS.toLocaleString() })}
        </div>
      )}
    </div>
  );
};

export default TextPreview;
