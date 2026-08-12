/** PDF 使用 pdf.js 逐页绘制到 canvas，不依赖 Electron 内置 PDF 插件。 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  RenderTask,
} from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import type { PreviewFilePayload } from '../../shared/bridge';
import { fetchPreviewArrayBuffer } from '../../store/filePreview';
import { PreviewError, PreviewLoading } from './PreviewStates';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

type ZoomMode = 'fit' | 0.5 | 1 | 1.5 | 2;

const ZOOM_LEVELS: Array<{ value: ZoomMode; label: string }> = [
  { value: 0.5, label: '50%' },
  { value: 1, label: '100%' },
  { value: 1.5, label: '150%' },
  { value: 2, label: '200%' },
  { value: 'fit', label: i18n.t('pdf.preview.09') },
];
const MAX_CANVAS_EDGE = 8_192;
const MAX_CANVAS_PIXELS = 16_000_000;

function readablePdfError(reason: unknown): string {
  if (reason instanceof Error && reason.message) return i18n.t('pdf.preview.03') + '：' + reason.message;
  return i18n.t('pdf.preview.03') + '。';
}

const PdfPreview: React.FC<{ file: PreviewFilePayload; projectId: string }> = ({
  file,
  projectId,
}) => {
  const { t } = useTranslation();
  const [documentProxy, setDocumentProxy] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [zoom, setZoom] = useState<ZoomMode>('fit');
  const [containerWidth, setContainerWidth] = useState(0);
  const [loadingDocument, setLoadingDocument] = useState(true);
  const [renderingPage, setRenderingPage] = useState(false);
  const [renderNotice, setRenderNotice] = useState('');
  const [error, setError] = useState('');
  const [retryToken, setRetryToken] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const loadingTaskRef = useRef<PDFDocumentLoadingTask | null>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return undefined;
    const measure = (): void => {
      const width = container.clientWidth;
      if (width > 0) setContainerWidth(width);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let alive = true;
    setDocumentProxy(null);
    setPageNumber(1);
    setLoadingDocument(true);
    setRenderNotice('');
    setError('');
    renderTaskRef.current?.cancel();
    renderTaskRef.current = null;

    void fetchPreviewArrayBuffer(projectId, file)
      .then((buffer) => {
        if (!alive) return null;
        const task = pdfjs.getDocument({ data: new Uint8Array(buffer) });
        loadingTaskRef.current = task;
        return task.promise;
      })
      .then((document) => {
        if (!alive || !document) {
          void document?.destroy();
          return;
        }
        setDocumentProxy(document);
        setLoadingDocument(false);
      })
      .catch((reason: unknown) => {
        if (!alive) return;
        setError(readablePdfError(reason));
        setLoadingDocument(false);
      });

    return () => {
      alive = false;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      const task = loadingTaskRef.current;
      loadingTaskRef.current = null;
      if (task) void task.destroy();
      const canvas = canvasRef.current;
      if (canvas) {
        canvas.width = 0;
        canvas.height = 0;
      }
    };
  }, [file.file_id, file.mtime_ns, file.path, projectId, retryToken]);

  useEffect(() => {
    const document = documentProxy;
    const canvas = canvasRef.current;
    if (!document || !canvas || containerWidth <= 0) return undefined;
    let alive = true;
    setRenderingPage(true);
    setError('');
    renderTaskRef.current?.cancel();

    void document.getPage(pageNumber)
      .then((page) => {
        if (!alive) return null;
        const baseViewport = page.getViewport({ scale: 1 });
        const scale = zoom === 'fit'
          ? Math.max(0.1, Math.min(4, (containerWidth - 40) / baseViewport.width))
          : zoom;
        const requestedViewport = page.getViewport({ scale });
        const logicalFactor = Math.min(
          1,
          MAX_CANVAS_EDGE / Math.max(1, requestedViewport.width),
          MAX_CANVAS_EDGE / Math.max(1, requestedViewport.height),
        );
        const viewport = page.getViewport({ scale: scale * logicalFactor });
        const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
        const physicalWidth = Math.max(1, viewport.width * pixelRatio);
        const physicalHeight = Math.max(1, viewport.height * pixelRatio);
        const pixelFactor = Math.min(
          1,
          MAX_CANVAS_EDGE / physicalWidth,
          MAX_CANVAS_EDGE / physicalHeight,
          Math.sqrt(MAX_CANVAS_PIXELS / (physicalWidth * physicalHeight)),
        );
        const renderViewport = page.getViewport({ scale: scale * logicalFactor * pixelFactor });
        const context = canvas.getContext('2d', { alpha: false });
        if (!context) throw new Error(t('pdf.preview.06') + '。');
        canvas.width = Math.max(1, Math.floor(renderViewport.width * pixelRatio));
        canvas.height = Math.max(1, Math.floor(renderViewport.height * pixelRatio));
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        setRenderNotice(logicalFactor < 1 || pixelFactor < 1
          ? t('pdf.preview.11') + '。'
          : '');
        const task = page.render({
          canvasContext: context,
          viewport: renderViewport,
          transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
        });
        renderTaskRef.current = task;
        return task.promise;
      })
      .then(() => {
        if (alive) setRenderingPage(false);
      })
      .catch((reason: unknown) => {
        if (!alive) return;
        const name = reason instanceof Error ? reason.name : '';
        if (name === 'RenderingCancelledException') return;
        setError(readablePdfError(reason));
        setRenderingPage(false);
      });

    return () => {
      alive = false;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
    };
  }, [containerWidth, documentProxy, pageNumber, zoom]);

  const pageCount = documentProxy?.numPages ?? 0;
  const pageLabel = useMemo(
    () => loadingDocument ? '— / —' : `${pageNumber} / ${Math.max(1, pageCount)}`,
    [loadingDocument, pageCount, pageNumber],
  );

  if (error && !documentProxy) {
    return <PreviewError message={error} onRetry={() => setRetryToken((value) => value + 1)} />;
  }

  return (
    <div className="pv-pdf-wrap">
      <div className="pv-pdf-toolbar">
        <div className="pv-pdf-pages" role="group" aria-label={t('pdf.preview.02')}>
          <button
            type="button"
            onClick={() => setPageNumber((value) => Math.max(1, value - 1))}
            disabled={loadingDocument || pageNumber <= 1}
            aria-label={t('pdf.preview.04')}
          >
            ‹
          </button>
          <span>{pageLabel}</span>
          <button
            type="button"
            onClick={() => setPageNumber((value) => Math.min(pageCount, value + 1))}
            disabled={loadingDocument || pageNumber >= pageCount}
            aria-label={t('pdf.preview.05')}
          >
            ›
          </button>
        </div>
        <div className="pv-pdf-zoom" role="group" aria-label={t('pdf.preview.01')}>
          {ZOOM_LEVELS.map((level) => (
            <button
              key={String(level.value)}
              type="button"
              className={zoom === level.value ? 'active' : ''}
              aria-pressed={zoom === level.value}
              onClick={() => setZoom(level.value)}
            >
              {level.label}
            </button>
          ))}
        </div>
      </div>
      {error && documentProxy && (
        <div className="pv-inline-error" role="alert">
          {error}
          <button type="button" onClick={() => setRetryToken((value) => value + 1)}>{t('pdf.preview.10')}</button>
        </div>
      )}
      {renderNotice && <div className="pv-pdf-notice" role="note">{renderNotice}</div>}
      <div ref={scrollRef} className="pv-pdf-canvas-scroll" tabIndex={0}>
        <div className="pv-pdf-canvas-stage">
          <canvas ref={canvasRef} className="pv-pdf-canvas" />
        </div>
        {(loadingDocument || renderingPage) && (
          <div className="pv-pdf-loading"><PreviewLoading label={loadingDocument ? t('pdf.preview.07') + '…' : t('pdf.preview.08') + '…'} /></div>
        )}
      </div>
    </div>
  );
};

export default PdfPreview;
