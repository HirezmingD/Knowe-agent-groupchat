/** 图片预览：滚轮缩放、放大后拖动、工具条复位。 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { PreviewFilePayload } from '../../shared/bridge';
import { previewUrl } from '../../store/filePreview';
import { IconFit, IconZoomIn, IconZoomOut } from '../icons';
import { PreviewError, PreviewLoading } from './PreviewStates';
import { useTranslation } from 'react-i18next';

const MIN_SCALE = 0.1;
const MAX_SCALE = 8;

const ImagePreview: React.FC<{ file: PreviewFilePayload; projectId: string }> = ({
  file,
  projectId,
}) => {
  const { t } = useTranslation();
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number; offsetX: number; offsetY: number } | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const url = previewUrl(projectId, file);

  const reset = useCallback(() => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  }, []);

  useEffect(() => {
    setStatus('loading');
    dragRef.current = null;
    reset();
  }, [reset, url]);

  const zoom = useCallback((factor: number): void => {
    setScale((value) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, value * factor)));
  }, []);

  const onWheel = useCallback((event: React.WheelEvent<HTMLDivElement>): void => {
    event.preventDefault();
    const image = imgRef.current;
    if (!image) return;
    const rect = image.getBoundingClientRect();
    const anchorX = event.clientX - (rect.left + rect.width / 2);
    const anchorY = event.clientY - (rect.top + rect.height / 2);
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    setScale((previous) => {
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, previous * factor));
      const ratio = next / previous;
      setOffset((current) => ({
        x: current.x - anchorX * (ratio - 1),
        y: current.y - anchorY * (ratio - 1),
      }));
      return next;
    });
  }, []);

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>): void => {
    if (scale <= 1) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      x: event.clientX,
      y: event.clientY,
      offsetX: offset.x,
      offsetY: offset.y,
    };
  }, [offset.x, offset.y, scale]);

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLDivElement>): void => {
    const drag = dragRef.current;
    if (!drag) return;
    setOffset({
      x: drag.offsetX + event.clientX - drag.x,
      y: drag.offsetY + event.clientY - drag.y,
    });
  }, []);

  const stopDragging = useCallback(() => { dragRef.current = null; }, []);

  return (
    <div className="pv-image-wrap">
      {status === 'loading' && <PreviewLoading label={t('image.preview.02')} />}
      {status === 'error' && <PreviewError message={t('image.preview.01')} />}
      <div
        className={`pv-image-stage${scale > 1 ? ' grabbable' : ''}`}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={stopDragging}
        onPointerCancel={stopDragging}
        onDoubleClick={reset}
      >
        <img
          key={url}
          ref={imgRef}
          className="pv-image"
          src={url}
          alt={file.name}
          draggable={false}
          hidden={status === 'error'}
          style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }}
          onLoad={() => setStatus('ready')}
          onError={() => setStatus('error')}
        />
      </div>
      {status === 'ready' && (
        <div className="pv-image-tools">
          <button type="button" onClick={() => zoom(1 / 1.25)} title={t('zoom.pan.04')} aria-label={t('zoom.pan.04')}>
            <IconZoomOut size={16} />
          </button>
          <button type="button" className="pv-zoom-pct" onClick={reset} title={t('zoom.pan.01')}>
            {Math.round(scale * 100)}%
          </button>
          <button type="button" onClick={() => zoom(1.25)} title={t('zoom.pan.03')} aria-label={t('zoom.pan.03')}>
            <IconZoomIn size={16} />
          </button>
          <button type="button" onClick={reset} title={t('zoom.pan.02')} aria-label={t('zoom.pan.02')}>
            <IconFit size={16} />
          </button>
        </div>
      )}
    </div>
  );
};

export default ImagePreview;
