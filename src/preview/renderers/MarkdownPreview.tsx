/** Markdown 只读预览；不启用原始 HTML，项目内导航始终受预览沙箱约束。 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';
import type { PreviewFilePayload } from '../../shared/bridge';
import {
  fetchPreviewText,
  previewUrl,
  resolveMarkdownRelativePath,
} from '../../store/filePreview';
import { PreviewError, PreviewLoading, useAsyncPreview } from './PreviewStates';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';

const MAX_MARKDOWN_CHARS = 2_000_000;
const MAX_MARKDOWN_LINES = 20_000;
const URL_SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]*:/;

interface MarkdownModel {
  text: string;
  totalLines: number;
  truncated: boolean;
}

interface MarkdownPreviewProps {
  file: PreviewFilePayload;
  projectId: string;
  fragment: string | null;
  fragmentRequest: number;
  onOpenRelative: (href: string) => void | Promise<void>;
}

async function loadMarkdown(
  projectId: string,
  file: PreviewFilePayload,
): Promise<MarkdownModel> {
  const raw = await fetchPreviewText(projectId, file);
  if (raw.includes('\u0000')) throw new Error(i18n.t('markdown.preview.07') + '。');
  const lines = raw.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split('\n');
  const totalLines = lines.length;
  let text = lines.slice(0, MAX_MARKDOWN_LINES).join('\n');
  let truncated = totalLines > MAX_MARKDOWN_LINES;
  if (text.length > MAX_MARKDOWN_CHARS) {
    text = text.slice(0, MAX_MARKDOWN_CHARS);
    truncated = true;
  }
  return { text, totalLines, truncated };
}

function readableError(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

function textFromNode(value: React.ReactNode): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (Array.isArray(value)) return value.map(textFromNode).join('');
  if (React.isValidElement<{ children?: React.ReactNode }>(value)) {
    return textFromNode(value.props.children);
  }
  return '';
}

/** Deterministic, Unicode-preserving heading slug without a third-party plugin. */
export function markdownHeadingSlug(value: string): string {
  const normalized = value
    .normalize('NFKC')
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, '')
    .replace(/[\s_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return normalized || 'section';
}

function isLoopbackHostname(raw: string): boolean {
  const host = raw.trim().toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (host === 'localhost' || host.endsWith('.localhost') || host === '::1' || host === '::') return true;
  if (
    host === '0.0.0.0'
    || host.startsWith('::ffff:127.')
    || /^::ffff:7f[0-9a-f]{2}(?::|$)/.test(host)
  ) return true;
  const octets = host.split('.');
  return octets.length === 4
    && octets.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255)
    && Number(octets[0]) === 127;
}

function safeExternalHref(rawHref: string): string | null {
  try {
    const url = new URL(rawHref);
    if (url.protocol === 'mailto:') return url.href;
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    return isLoopbackHostname(url.hostname) ? null : url.href;
  } catch {
    return null;
  }
}

// Backward-compatible test/import surface; the authoritative implementation lives with all
// preview path and identity functions in filePreview.ts.
export { resolveMarkdownRelativePath } from '../../store/filePreview';

function imagePreviewUrl(
  projectId: string,
  currentFilePath: string,
  rawSource: string,
): string {
  const target = resolveMarkdownRelativePath(currentFilePath, rawSource);
  const pathParts = target.path.split('/');
  const name = pathParts[pathParts.length - 1] || target.path;
  const url = previewUrl(projectId, {
    path: target.path,
    source_path: target.path,
    name,
  });
  return url;
}

const MarkdownPreview: React.FC<MarkdownPreviewProps> = ({
  file,
  projectId,
  fragment,
  fragmentRequest,
  onOpenRelative,
}) => {
  const { t } = useTranslation();
  const articleRef = useRef<HTMLElement>(null);
  const [linkError, setLinkError] = useState('');
  const { status, data, error, reload } = useAsyncPreview(
    () => loadMarkdown(projectId, file),
    [projectId, file.path, file.file_id, file.mtime_ns],
  );

  useEffect(() => {
    setLinkError('');
  }, [file.path, projectId]);

  const scrollToAnchor = useCallback((rawHash: string, behavior: ScrollBehavior = 'smooth'): void => {
    const article = articleRef.current;
    if (!article) return;
    let decoded: string;
    try {
      decoded = decodeURIComponent(rawHash.replace(/^#/, ''));
    } catch {
      setLinkError(t('markdown.preview.05') + '。');
      return;
    }
    if (!decoded) {
      article.scrollTo({ top: 0, behavior });
      setLinkError('');
      return;
    }
    const candidates = new Set([decoded, markdownHeadingSlug(decoded)]);
    const target = Array.from(article.querySelectorAll<HTMLElement>('[id]'))
      .find((element) => candidates.has(element.id));
    if (!target) {
      setLinkError(t('markdown.preview.anchorNotFound', { name: decoded }));
      return;
    }
    target.scrollIntoView({ behavior, block: 'start' });
    setLinkError('');
  }, []);

  useEffect(() => {
    if (status !== 'ready' || fragment === null) return undefined;
    // Wait one paint so ReactMarkdown has committed every heading id before querying the article.
    const frame = window.requestAnimationFrame(() => scrollToAnchor(fragment, 'auto'));
    return () => window.cancelAnimationFrame(frame);
  }, [data?.text, fragment, fragmentRequest, scrollToAnchor, status]);

  if (status === 'loading') return <PreviewLoading label={t('markdown.preview.04') + '…'} />;
  if (status === 'error') return <PreviewError message={error} onRetry={reload} />;

  const slugCounts = new Map<string, number>();
  const nextHeadingId = (children: React.ReactNode): string => {
    const base = markdownHeadingSlug(textFromNode(children));
    const seen = slugCounts.get(base) ?? 0;
    slugCounts.set(base, seen + 1);
    return seen === 0 ? base : `${base}-${seen}`;
  };

  const showLinkError = (reason: unknown, fallback: string): void => {
    setLinkError(readableError(reason, fallback));
  };

  return (
    <article ref={articleRef} className="pv-md">
      {data?.truncated && (
        <div className="pv-md-truncated" role="note">
          {t('markdown.preview.truncated', { n: MAX_MARKDOWN_LINES.toLocaleString(), m: MAX_MARKDOWN_CHARS.toLocaleString() })}
          {t('markdown.preview.totalLines', { n: data.totalLines.toLocaleString() })}
        </div>
      )}
      {linkError && <div className="pv-md-truncated" role="alert">{linkError}</div>}
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]}
        rehypePlugins={[rehypeHighlight, rehypeKatex]}
        components={{
          h1: ({ node: _node, children, ...props }) => (
            <h1 {...props} id={nextHeadingId(children)}>{children}</h1>
          ),
          h2: ({ node: _node, children, ...props }) => (
            <h2 {...props} id={nextHeadingId(children)}>{children}</h2>
          ),
          h3: ({ node: _node, children, ...props }) => (
            <h3 {...props} id={nextHeadingId(children)}>{children}</h3>
          ),
          h4: ({ node: _node, children, ...props }) => (
            <h4 {...props} id={nextHeadingId(children)}>{children}</h4>
          ),
          h5: ({ node: _node, children, ...props }) => (
            <h5 {...props} id={nextHeadingId(children)}>{children}</h5>
          ),
          h6: ({ node: _node, children, ...props }) => (
            <h6 {...props} id={nextHeadingId(children)}>{children}</h6>
          ),
          a: ({ node: _node, href = '', children, ...props }) => {
            if (href.startsWith('#')) {
              return (
                <a
                  {...props}
                  href={href}
                  onClick={(event) => {
                    event.preventDefault();
                    scrollToAnchor(href);
                  }}
                >
                  {children}
                </a>
              );
            }

            const external = safeExternalHref(href);
            if (external) {
              return (
                <a {...props} href={external} target="_blank" rel="noopener noreferrer">
                  {children}
                </a>
              );
            }

            const unsafe = (
              !href
              || URL_SCHEME_RE.test(href)
              || href.startsWith('//')
              || href.startsWith('/')
              || href.startsWith('\\')
            );
            if (unsafe) {
              return (
                <a
                  {...props}
                  href={href || undefined}
                  aria-disabled="true"
                  onClick={(event) => {
                    event.preventDefault();
                    setLinkError(t('markdown.preview.06') + '。');
                  }}
                >
                  {children}
                </a>
              );
            }

            return (
              <a
                {...props}
                href={href}
                onClick={(event) => {
                  event.preventDefault();
                  setLinkError('');
                  try {
                    void Promise.resolve(onOpenRelative(href)).catch((reason: unknown) => {
                      showLinkError(reason, t('markdown.preview.03') + '。');
                    });
                  } catch (reason) {
                    showLinkError(reason, t('markdown.preview.03') + '。');
                  }
                }}
              >
                {children}
              </a>
            );
          },
          img: ({ node: _node, src = '', alt = '', ...props }) => {
            try {
              const resolved = imagePreviewUrl(projectId, file.path, src);
              return <img {...props} src={resolved} alt={alt} loading="lazy" />;
            } catch (reason) {
              const message = readableError(reason, t('markdown.preview.02') + '。');
              return <span role="img" aria-label={message} title={message}>{alt || t('markdown.preview.01')}</span>;
            }
          },
        }}
      >
        {data?.text || ''}
      </ReactMarkdown>
    </article>
  );
};

export default MarkdownPreview;
