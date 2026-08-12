/** 只读代码预览：零依赖词法着色，并对大文件做硬上限保护。 */

import React, { useMemo } from 'react';
import type { PreviewFilePayload } from '../../shared/bridge';
import { fetchPreviewText } from '../../store/filePreview';
import { PreviewError, PreviewLoading, useAsyncPreview } from './PreviewStates';
import {
  codeLanguageFor,
  codeLanguageLabel,
  highlightCode,
  type CodeLanguage,
} from './codeSyntax';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';

const MAX_CODE_CHARS = 2_000_000;
const MAX_CODE_LINES = 20_000;

interface PreparedCode {
  language: CodeLanguage;
  lines: ReturnType<typeof highlightCode>;
  totalLines: number;
  truncated: boolean;
}

function prepareCode(text: string, language: CodeLanguage): PreparedCode {
  if (text.includes('\u0000')) throw new Error(i18n.t('code.preview.03'));
  const originalLines = text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split('\n');
  const totalLines = originalLines.length;
  let clipped = originalLines.slice(0, MAX_CODE_LINES).join('\n');
  let truncated = totalLines > MAX_CODE_LINES;
  if (clipped.length > MAX_CODE_CHARS) {
    clipped = clipped.slice(0, MAX_CODE_CHARS);
    truncated = true;
  }
  return { language, lines: highlightCode(clipped, language), totalLines, truncated };
}

const CodePreview: React.FC<{ file: PreviewFilePayload; projectId: string }> = ({
  file,
  projectId,
}) => {
  const { t } = useTranslation();
  const language = codeLanguageFor(file.name, file.ext);
  const { status, data, error, reload } = useAsyncPreview(
    () => fetchPreviewText(projectId, file),
    [projectId, file.path, file.file_id, file.mtime_ns],
  );

  const prepared = useMemo(() => {
    if (!language || status !== 'ready') return null;
    try {
      return { value: prepareCode(data || '', language), error: '' };
    } catch (reason) {
      return {
        value: null,
        error: reason instanceof Error ? reason.message : t('code.preview.01'),
      };
    }
  }, [data, language, status]);

  if (!language) return <PreviewError message={t('code.preview.04')} />;
  if (status === 'loading') return <PreviewLoading label={t('code.preview.02')} />;
  if (status === 'error') return <PreviewError message={error} onRetry={reload} />;
  if (!prepared?.value) {
    return <PreviewError message={prepared?.error || t('code.preview.01')} onRetry={reload} />;
  }

  const model = prepared.value;
  return (
    <div className="pv-code">
      <div className="pv-code-meta">
        <span className="pv-code-language">{codeLanguageLabel(model.language)}</span>
        <span className="pv-code-count">{t('common.lineCount', { n: model.totalLines.toLocaleString() })}</span>
      </div>
      <div
        className="pv-code-scroll"
        role="region"
        aria-label={t('code.preview.readonly', { lang: codeLanguageLabel(model.language) })}
        tabIndex={0}
      >
        <ol className="pv-code-lines">
          {model.lines.map((line, lineIndex) => (
            <li className="pv-code-line" key={lineIndex} value={lineIndex + 1}>
              <code>
                {line.tokens.length === 0 ? '\u200b' : line.tokens.map((token, tokenIndex) => (
                  <span className={`tok-${token.kind}`} key={tokenIndex}>{token.text}</span>
                ))}
              </code>
            </li>
          ))}
        </ol>
        {model.truncated && (
          <div className="pv-code-truncated" role="note">
            {t('code.preview.truncated', { n: MAX_CODE_LINES.toLocaleString(), m: MAX_CODE_CHARS.toLocaleString() })}
          </div>
        )}
      </div>
    </div>
  );
};

export default CodePreview;
