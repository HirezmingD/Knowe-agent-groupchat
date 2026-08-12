/** DOCX 经 Mammoth 转换后放入无脚本、无同源权限的 srcDoc iframe。 */

import React from 'react';
import type { PreviewFilePayload } from '../../shared/bridge';
import { fetchPreviewArrayBuffer } from '../../store/filePreview';
import { PreviewError, PreviewLoading, useAsyncPreview } from './PreviewStates';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';

const MAX_DOCX_NODES = 20_000;
const MAX_DOCX_TEXT_CHARS = 2_000_000;
const MAX_DOCX_IMAGE_BASE64_CHARS = 8_000_000;
const MAX_DOCX_TOTAL_IMAGE_BASE64_CHARS = 16_000_000;

const DOCUMENT_STYLE = `
  :root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  body { max-width: 860px; margin: 0 auto; padding: 42px 54px 72px; color: #24211f; line-height: 1.72; overflow-wrap: anywhere; }
  h1,h2,h3,h4 { line-height: 1.3; margin: 1.15em 0 .45em; }
  p { margin: .65em 0; } ul,ol { padding-left: 1.6em; }
  table { border-collapse: collapse; width: 100%; display: block; overflow-x: auto; }
  th,td { border: 1px solid #d9d4ce; padding: 6px 10px; text-align: left; vertical-align: top; }
  th { background: #f5f3f0; } img { max-width: 100%; height: auto; }
  blockquote { margin: 1em 0; padding-left: 1em; border-left: 3px solid #d9d4ce; color: #625c57; }
  pre { overflow: auto; padding: 12px; background: #f5f3f0; } a { color: #315ea8; }
`;

function wrapDocument(content: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><style>${DOCUMENT_STYLE}</style></head><body>${content || i18n.t('docx.preview.01')}</body></html>`;
}

function limitDocumentHtml(content: string, omittedImages: number): string {
  const document = new DOMParser().parseFromString(content, 'text/html');
  const { body } = document;
  let truncated = omittedImages > 0;
  const elements = Array.from(body.querySelectorAll('*'));
  if (elements.length > MAX_DOCX_NODES) {
    truncated = true;
    for (const element of elements.slice(MAX_DOCX_NODES).reverse()) element.remove();
  }

  let remaining = MAX_DOCX_TEXT_CHARS;
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();
  while (current) {
    const text = current.nodeValue || '';
    if (remaining <= 0) {
      if (text) truncated = true;
      current.nodeValue = '';
    } else if (text.length > remaining) {
      current.nodeValue = text.slice(0, remaining);
      remaining = 0;
      truncated = true;
    } else {
      remaining -= text.length;
    }
    current = walker.nextNode();
  }

  if (truncated) {
    const note = document.createElement('p');
    note.setAttribute('role', 'note');
    note.style.cssText = 'margin-top:24px;padding:10px 12px;background:#f5f3f0;color:#625c57;border-radius:6px';
    note.textContent = omittedImages > 0
      ? i18n.t('docx.preview.limited', { n: omittedImages })
      : i18n.t('docx.preview.03');
    body.append(note);
  }
  return body.innerHTML;
}

async function loadDocx(
  projectId: string,
  file: PreviewFilePayload,
): Promise<string> {
  const buffer = await fetchPreviewArrayBuffer(projectId, file);
  const imported = await import('mammoth');
  const mammoth = imported.default ?? imported;
  let remainingImageBudget = MAX_DOCX_TOTAL_IMAGE_BASE64_CHARS;
  let omittedImages = 0;
  const convertImage = mammoth.images.imgElement(async (image) => {
    const content = await image.read('base64');
    if (
      content.length > MAX_DOCX_IMAGE_BASE64_CHARS
      || content.length > remainingImageBudget
    ) {
      omittedImages += 1;
      return { src: 'data:,', alt: i18n.t('docx.preview.02') };
    }
    remainingImageBudget -= content.length;
    return {
      src: `data:${image.contentType || 'application/octet-stream'};base64,${content}`,
    };
  });
  const result = await mammoth.convertToHtml(
    { arrayBuffer: buffer },
    { convertImage },
  );
  return wrapDocument(limitDocumentHtml(String(result.value || '').trim(), omittedImages));
}

const DocxPreview: React.FC<{ file: PreviewFilePayload; projectId: string }> = ({
  file,
  projectId,
}) => {
  const { t } = useTranslation();
  const { status, data, error, reload } = useAsyncPreview(
    () => loadDocx(projectId, file),
    [projectId, file.path, file.file_id, file.mtime_ns],
  );

  if (status === 'loading') return <PreviewLoading label={t('docx.preview.04')} />;
  if (status === 'error') return <PreviewError message={error} onRetry={reload} />;
  return (
    <iframe
      className="pv-docx-frame"
      title={file.name}
      sandbox=""
      referrerPolicy="no-referrer"
      srcDoc={data || wrapDocument('')}
    />
  );
};

export default DocxPreview;
