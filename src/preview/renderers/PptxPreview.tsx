/**
 * PPTX 轻量版式预览：按真实页序读取 OOXML，保留常见文本格式、图片与绝对位置。
 * 图表、SmartArt、动画、视频和母版继承不建立新引擎，而在对应页面明确提示降级。
 */

import React, { useEffect, useRef, useState } from 'react';
import type JSZip from 'jszip';
import type { PreviewFilePayload } from '../../shared/bridge';
import { fetchPreviewArrayBuffer } from '../../store/filePreview';
import { PreviewError, PreviewLoading } from './PreviewStates';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';

interface SlideSize {
  cx: number;
  cy: number;
}

interface BoxModel {
  x: number;
  y: number;
  cx: number;
  cy: number;
  rotation: number;
}

interface TextRunModel {
  text: string;
  sizePt: number;
  bold: boolean;
  italic: boolean;
  color: string;
  family: string;
}

interface ParagraphModel {
  align: React.CSSProperties['textAlign'];
  bullet: string;
  runs: TextRunModel[];
}

interface TextElementModel {
  kind: 'text';
  box: BoxModel;
  paragraphs: ParagraphModel[];
  fill: string;
  border: string;
  vertical: React.CSSProperties['justifyContent'];
}

interface ImageElementModel {
  kind: 'image';
  box: BoxModel;
  url: string;
  alt: string;
}

type SlideElementModel = TextElementModel | ImageElementModel;

interface SlideModel {
  elements: SlideElementModel[];
  degraded: string[];
  objectUrls: string[];
}

export interface PresentationModel {
  size: SlideSize;
  slides: SlideModel[];
  objectUrls: string[];
  orderFallback: boolean;
  omittedSlides: number;
}

const RELATIONSHIP_NAMESPACE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const DEFAULT_SIZE: SlideSize = { cx: 12_192_000, cy: 6_858_000 };
const MAX_SLIDES = 200;
const MAX_ELEMENTS_PER_SLIDE = 500;
const MAX_PARAGRAPHS_PER_SHAPE = 200;
const MAX_RUNS_PER_PARAGRAPH = 400;
const MAX_TEXT_CHARS_PER_SLIDE = 200_000;
const MAX_IMAGE_BYTES = 16 * 1024 * 1024;
const THEME_COLORS: Record<string, string> = {
  dk1: '#000000', lt1: '#ffffff', dk2: '#1f497d', lt2: '#eee9e1',
  accent1: '#4472c4', accent2: '#ed7d31', accent3: '#a5a5a5',
  accent4: '#ffc000', accent5: '#5b9bd5', accent6: '#70ad47',
  hlink: '#0563c1', folHlink: '#954f72',
};

function parseXml(xml: string, label: string): Document {
  const document = new DOMParser().parseFromString(xml, 'application/xml');
  if (document.getElementsByTagName('parsererror').length > 0) {
    throw new Error(i18n.t('pptx.preview.xmlCorrupt', { label }));
  }
  return document;
}

function descendants(root: ParentNode, localName: string): Element[] {
  return Array.from(root.querySelectorAll('*')).filter((element) => element.localName === localName);
}

function firstDescendant(root: ParentNode | null, localName: string): Element | null {
  if (!root) return null;
  return descendants(root, localName)[0] ?? null;
}

function directChild(root: Element | null, localName: string): Element | null {
  if (!root) return null;
  return Array.from(root.children).find((child) => child.localName === localName) ?? null;
}

function finiteNumber(value: string | null, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function relationshipId(element: Element): string {
  return element.getAttributeNS(RELATIONSHIP_NAMESPACE, 'id')
    || element.getAttribute('r:id')
    || '';
}

function embeddedRelationshipId(element: Element): string {
  return element.getAttributeNS(RELATIONSHIP_NAMESPACE, 'embed')
    || element.getAttribute('r:embed')
    || '';
}

function normalizeZipPath(path: string): string {
  const output: string[] = [];
  for (const segment of path.replace(/\\/g, '/').split('/')) {
    if (!segment || segment === '.') continue;
    if (segment === '..') {
      if (output.length === 0) throw new Error(i18n.t('pptx.preview.11') + '。');
      output.pop();
      continue;
    }
    output.push(segment);
  }
  return output.join('/');
}

function resolveZipPath(baseFile: string, target: string): string {
  if (/^[a-z]+:/i.test(target) || target.startsWith('/')) {
    throw new Error(i18n.t('pptx.preview.10') + '。');
  }
  const slash = baseFile.lastIndexOf('/');
  const directory = slash >= 0 ? baseFile.slice(0, slash + 1) : '';
  return normalizeZipPath(`${directory}${target}`);
}

function relationshipFile(path: string): string {
  const slash = path.lastIndexOf('/');
  const directory = slash >= 0 ? path.slice(0, slash + 1) : '';
  const name = slash >= 0 ? path.slice(slash + 1) : path;
  return `${directory}_rels/${name}.rels`;
}

export function parseSlideSize(xml: Document): SlideSize {
  const size = descendants(xml, 'sldSz')[0];
  if (!size) return DEFAULT_SIZE;
  const cx = finiteNumber(size.getAttribute('cx'), DEFAULT_SIZE.cx);
  const cy = finiteNumber(size.getAttribute('cy'), DEFAULT_SIZE.cy);
  return cx > 0 && cy > 0 ? { cx, cy } : DEFAULT_SIZE;
}

export function parseRelationships(xml: string): Map<string, string> {
  const document = parseXml(xml, i18n.t('pptx.preview.02'));
  const relationships = new Map<string, string>();
  for (const relation of descendants(document, 'Relationship')) {
    const id = relation.getAttribute('Id') || '';
    const target = relation.getAttribute('Target') || '';
    const external = (relation.getAttribute('TargetMode') || '').toLowerCase() === 'external';
    if (id && target && !external) relationships.set(id, target);
  }
  return relationships;
}

function parseBox(element: Element): BoxModel | null {
  const shapeProperties = directChild(element, 'spPr') || firstDescendant(element, 'spPr');
  const transform = firstDescendant(shapeProperties, 'xfrm');
  const offset = directChild(transform, 'off') || firstDescendant(transform, 'off');
  const extent = directChild(transform, 'ext') || firstDescendant(transform, 'ext');
  if (!offset || !extent) return null;
  const cx = finiteNumber(extent.getAttribute('cx'));
  const cy = finiteNumber(extent.getAttribute('cy'));
  if (cx <= 0 || cy <= 0) return null;
  return {
    x: finiteNumber(offset.getAttribute('x')),
    y: finiteNumber(offset.getAttribute('y')),
    cx,
    cy,
    rotation: finiteNumber(transform?.getAttribute('rot') ?? null) / 60_000,
  };
}

function fallbackBox(index: number, size: SlideSize): BoxModel {
  const row = index % 6;
  return {
    x: size.cx * 0.08,
    y: size.cy * (0.08 + row * 0.14),
    cx: size.cx * 0.84,
    cy: size.cy * 0.11,
    rotation: 0,
  };
}

function colorFrom(root: ParentNode | null, fallback: string, degraded: Set<string>): string {
  if (!root) return fallback;
  const solidFill = firstDescendant(root, 'solidFill');
  if (!solidFill) return fallback;
  const srgb = firstDescendant(solidFill, 'srgbClr')?.getAttribute('val');
  if (srgb && /^[0-9a-f]{6}$/i.test(srgb)) return `#${srgb}`;
  const system = firstDescendant(solidFill, 'sysClr');
  const systemValue = system?.getAttribute('lastClr') || system?.getAttribute('val');
  if (systemValue && /^[0-9a-f]{6}$/i.test(systemValue)) return `#${systemValue}`;
  const scheme = firstDescendant(solidFill, 'schemeClr')?.getAttribute('val') || '';
  const themeColor = scheme ? THEME_COLORS[scheme] : undefined;
  if (themeColor) {
    degraded.add(i18n.t('pptx.preview.01'));
    return themeColor;
  }
  return fallback;
}

function paragraphAlignment(value: string | null): React.CSSProperties['textAlign'] {
  switch (value) {
    case 'ctr': return 'center';
    case 'r': return 'right';
    case 'just': case 'justLow': case 'dist': return 'justify';
    default: return 'left';
  }
}

function verticalAlignment(value: string | null): React.CSSProperties['justifyContent'] {
  switch (value) {
    case 'ctr': return 'center';
    case 'b': return 'flex-end';
    default: return 'flex-start';
  }
}

function booleanAttribute(value: string | null): boolean {
  return value === '1' || value === 'true';
}

function takeTextWithinBudget(
  text: string,
  budget: { remaining: number },
  degraded: Set<string>,
): string {
  if (budget.remaining <= 0) {
    degraded.add(i18n.t('pptx.preview.textOmitted', { n: MAX_TEXT_CHARS_PER_SLIDE }));
    return '';
  }
  if (text.length <= budget.remaining) {
    budget.remaining -= text.length;
    return text;
  }
  const clipped = text.slice(0, budget.remaining);
  budget.remaining = 0;
  degraded.add(i18n.t('pptx.preview.textOmitted', { n: MAX_TEXT_CHARS_PER_SLIDE }));
  return clipped;
}

function parseTextShape(
  element: Element,
  box: BoxModel,
  degraded: Set<string>,
  textBudget: { remaining: number },
): TextElementModel | null {
  const textBody = directChild(element, 'txBody') || firstDescendant(element, 'txBody');
  if (!textBody) return null;
  const bodyProperties = directChild(textBody, 'bodyPr');
  const paragraphs: ParagraphModel[] = [];
  const paragraphElements = Array.from(textBody.children)
    .filter((child) => child.localName === 'p');
  if (paragraphElements.length > MAX_PARAGRAPHS_PER_SHAPE) {
    degraded.add(i18n.t('pptx.preview.parasOmitted', { n: MAX_PARAGRAPHS_PER_SHAPE }));
  }
  for (const paragraph of paragraphElements.slice(0, MAX_PARAGRAPHS_PER_SHAPE)) {
    if (textBudget.remaining <= 0) break;
    const paragraphProperties = directChild(paragraph, 'pPr');
    const bulletElement = firstDescendant(paragraphProperties, 'buChar');
    const bullet = bulletElement?.getAttribute('char') || '';
    const runs: TextRunModel[] = [];
    const runElements = Array.from(paragraph.children)
      .filter((child) => child.localName === 'r' || child.localName === 'fld');
    if (runElements.length > MAX_RUNS_PER_PARAGRAPH) {
      degraded.add(i18n.t('pptx.preview.runsOmitted', { n: MAX_RUNS_PER_PARAGRAPH }));
    }
    for (const child of runElements.slice(0, MAX_RUNS_PER_PARAGRAPH)) {
      const rawText = firstDescendant(child, 't')?.textContent || '';
      const text = takeTextWithinBudget(rawText, textBudget, degraded);
      if (!text) {
        if (textBudget.remaining <= 0) break;
        continue;
      }
      const properties = directChild(child, 'rPr') || firstDescendant(child, 'rPr');
      const sizeHundredths = finiteNumber(properties?.getAttribute('sz') ?? null, 1800);
      const latin = firstDescendant(properties, 'latin')?.getAttribute('typeface') || '';
      runs.push({
        text,
        sizePt: Math.max(1, sizeHundredths / 100),
        bold: booleanAttribute(properties?.getAttribute('b') || null),
        italic: booleanAttribute(properties?.getAttribute('i') || null),
        color: colorFrom(properties, '#222222', degraded),
        family: latin,
      });
    }
    if (runs.length === 0 && textBudget.remaining > 0) {
      const rawText = descendants(paragraph, 't')
        .slice(0, MAX_RUNS_PER_PARAGRAPH)
        .map((node) => node.textContent || '')
        .join('');
      const text = takeTextWithinBudget(rawText, textBudget, degraded);
      if (text) {
        const endProperties = directChild(paragraph, 'endParaRPr');
        const sizeHundredths = finiteNumber(endProperties?.getAttribute('sz') ?? null, 1800);
        runs.push({
          text,
          sizePt: Math.max(1, sizeHundredths / 100),
          bold: booleanAttribute(endProperties?.getAttribute('b') || null),
          italic: booleanAttribute(endProperties?.getAttribute('i') || null),
          color: colorFrom(endProperties, '#222222', degraded),
          family: firstDescendant(endProperties, 'latin')?.getAttribute('typeface') || '',
        });
      }
    }
    if (runs.length > 0) {
      paragraphs.push({
        align: paragraphAlignment(paragraphProperties?.getAttribute('algn') || null),
        bullet,
        runs,
      });
    }
  }
  if (paragraphs.length === 0) return null;

  const shapeProperties = directChild(element, 'spPr') || firstDescendant(element, 'spPr');
  const line = firstDescendant(shapeProperties, 'ln');
  return {
    kind: 'text',
    box,
    paragraphs,
    fill: colorFrom(shapeProperties, 'transparent', degraded),
    border: line ? colorFrom(line, 'rgba(0,0,0,.18)', degraded) : 'transparent',
    vertical: verticalAlignment(bodyProperties?.getAttribute('anchor') || null),
  };
}

function mimeForPath(path: string): string {
  const extension = path.split('.').pop()?.toLowerCase();
  switch (extension) {
    case 'png': return 'image/png';
    case 'jpg': case 'jpeg': return 'image/jpeg';
    case 'gif': return 'image/gif';
    case 'svg': return 'image/svg+xml';
    case 'webp': return 'image/webp';
    case 'bmp': return 'image/bmp';
    case 'emf': return 'image/emf';
    case 'wmf': return 'image/wmf';
    default: return 'application/octet-stream';
  }
}

function nonVisualName(element: Element): string {
  const properties = firstDescendant(element, 'cNvPr');
  return properties?.getAttribute('descr') || properties?.getAttribute('name') || i18n.t('pptx.preview.06');
}

export async function parseSlide(
  slideXml: string,
  rels: Map<string, string>,
  zip: JSZip,
  size: SlideSize,
): Promise<SlideModel> {
  const document = parseXml(slideXml, i18n.t('pptx.preview.05'));
  const elements: SlideElementModel[] = [];
  const degraded = new Set<string>();
  const objectUrls: string[] = [];
  const textBudget = { remaining: MAX_TEXT_CHARS_PER_SLIDE };
  const shapeTree = descendants(document, 'spTree')[0];
  if (!shapeTree) return { elements, degraded: [i18n.t('pptx.preview.22')], objectUrls };

  try {
    const children = Array.from(shapeTree.children);
    if (children.length > MAX_ELEMENTS_PER_SLIDE) {
      degraded.add(i18n.t('pptx.preview.elemsOmitted', { n: MAX_ELEMENTS_PER_SLIDE }));
    }
    let fallbackIndex = 0;
    for (const element of children.slice(0, MAX_ELEMENTS_PER_SLIDE)) {
      if (element.localName === 'sp') {
        let box = parseBox(element);
        if (!box) {
          box = fallbackBox(fallbackIndex, size);
          fallbackIndex += 1;
          degraded.add(i18n.t('pptx.preview.17'));
        }
        const textShape = parseTextShape(element, box, degraded, textBudget);
        if (textShape) elements.push(textShape);
        else degraded.add(i18n.t('pptx.preview.19'));
        continue;
      }

      if (element.localName === 'pic') {
        const box = parseBox(element);
        const blip = firstDescendant(element, 'blip');
        const id = blip ? embeddedRelationshipId(blip) : '';
        const mediaPath = id ? rels.get(id) : undefined;
        const media = mediaPath ? zip.file(mediaPath) : null;
        if (!box || !media || !mediaPath) {
          degraded.add(i18n.t('pptx.preview.18'));
          continue;
        }
        const bytes = await media.async('uint8array');
        if (bytes.byteLength > MAX_IMAGE_BYTES) {
          degraded.add(i18n.t('pptx.preview.20'));
          continue;
        }
        const copied = bytes.slice().buffer as ArrayBuffer;
        const url = URL.createObjectURL(new Blob([copied], { type: mimeForPath(mediaPath) }));
        objectUrls.push(url);
        elements.push({ kind: 'image', box, url, alt: nonVisualName(element) });
        continue;
      }

      if (element.localName === 'graphicFrame') {
        degraded.add(i18n.t('pptx.preview.04'));
      } else if (element.localName === 'grpSp') {
        degraded.add(i18n.t('pptx.preview.14'));
      } else if (element.localName === 'cxnSp') {
        degraded.add(i18n.t('pptx.preview.16'));
      }
    }

    if (descendants(document, 'transition').length > 0 || descendants(document, 'timing').length > 0) {
      degraded.add(i18n.t('pptx.preview.03'));
    }
    if (descendants(document, 'video').length > 0 || descendants(document, 'audio').length > 0) {
      degraded.add(i18n.t('pptx.preview.21'));
    }

    return { elements, degraded: Array.from(degraded), objectUrls };
  } catch (reason) {
    for (const url of objectUrls) URL.revokeObjectURL(url);
    throw reason;
  }
}

function numericSlidePath(path: string): number {
  const match = /slide(\d+)\.xml$/i.exec(path);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

async function readZipText(zip: JSZip, path: string, label: string): Promise<string> {
  const entry = zip.file(path);
  if (!entry) throw new Error(i18n.t('pptx.preview.missingPart', { label }));
  return entry.async('text');
}

function resolveRelationshipMap(
  relationships: Map<string, string>,
  baseFile: string,
): Map<string, string> {
  const resolved = new Map<string, string>();
  for (const [id, target] of relationships) {
    try {
      resolved.set(id, resolveZipPath(baseFile, target));
    } catch {
      // 外部或越界关系不交给后续媒体读取。
    }
  }
  return resolved;
}

export async function loadPresentation(
  projectId: string,
  file: PreviewFilePayload,
): Promise<PresentationModel> {
  const objectUrls: string[] = [];
  try {
    const buffer = await fetchPreviewArrayBuffer(projectId, file);
    const imported = await import('jszip');
    const JSZipConstructor = imported.default;
    const zip = await JSZipConstructor.loadAsync(buffer);
    const presentationPath = 'ppt/presentation.xml';
    const presentationXml = await readZipText(zip, presentationPath, i18n.t('pptx.preview.12'));
    const presentationDocument = parseXml(presentationXml, i18n.t('pptx.preview.09'));
    const size = parseSlideSize(presentationDocument);

    const presentationRelsPath = relationshipFile(presentationPath);
    const presentationRelsEntry = zip.file(presentationRelsPath);
    const presentationRels = presentationRelsEntry
      ? resolveRelationshipMap(parseRelationships(await presentationRelsEntry.async('text')), presentationPath)
      : new Map<string, string>();

    const orderedPaths = descendants(presentationDocument, 'sldId')
      .map((element) => presentationRels.get(relationshipId(element)) || '')
      .filter((path) => /^ppt\/slides\/slide\d+\.xml$/i.test(path));
    const fallbackPaths = Object.keys(zip.files)
      .filter((path) => /^ppt\/slides\/slide\d+\.xml$/i.test(path))
      .sort((left, right) => numericSlidePath(left) - numericSlidePath(right));
    const orderFallback = orderedPaths.length === 0;
    const slidePaths = orderFallback ? fallbackPaths : orderedPaths;
    if (slidePaths.length === 0) throw new Error(i18n.t('pptx.preview.15') + '。');
    const omittedSlides = Math.max(0, slidePaths.length - MAX_SLIDES);

    const slides: SlideModel[] = [];
    for (const slidePath of slidePaths.slice(0, MAX_SLIDES)) {
      const slideXml = await readZipText(zip, slidePath, i18n.t('pptx.preview.pageLabel', { path: slidePath }));
      const relsPath = relationshipFile(slidePath);
      const relsEntry = zip.file(relsPath);
      const rels = relsEntry
        ? resolveRelationshipMap(parseRelationships(await relsEntry.async('text')), slidePath)
        : new Map<string, string>();
      const slide = await parseSlide(slideXml, rels, zip, size);
      objectUrls.push(...slide.objectUrls);
      slides.push(slide);
    }
    return { size, slides, objectUrls, orderFallback, omittedSlides };
  } catch (reason) {
    for (const url of objectUrls) URL.revokeObjectURL(url);
    throw reason;
  }
}

function percent(value: number, total: number): string {
  return `${Math.max(-200, Math.min(300, value / total * 100))}%`;
}

function renderTextRun(run: TextRunModel, index: number, size: SlideSize): React.ReactNode {
  const fontPercent = Math.max(0.35, run.sizePt * 12_700 / size.cx * 100);
  return (
    <span
      key={index}
      style={{
        color: run.color,
        fontFamily: run.family || undefined,
        fontSize: `clamp(6px, ${fontPercent}cqw, 72px)`,
        fontWeight: run.bold ? 700 : 400,
        fontStyle: run.italic ? 'italic' : 'normal',
      }}
    >
      {run.text}
    </span>
  );
}

const PptxPreview: React.FC<{ file: PreviewFilePayload; projectId: string }> = ({
  file,
  projectId,
}) => {
  const { t } = useTranslation();
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [model, setModel] = useState<PresentationModel | null>(null);
  const [error, setError] = useState('');
  const [nonce, setNonce] = useState(0);
  const runRef = useRef(0);

  useEffect(() => {
    const run = ++runRef.current;
    let alive = true;
    setStatus('loading');
    setError('');
    setModel(null);
    void loadPresentation(projectId, file)
      .then((nextModel) => {
        if (!alive || run !== runRef.current) {
          for (const url of nextModel.objectUrls) URL.revokeObjectURL(url);
          return;
        }
        setModel(nextModel);
        setStatus('ready');
      })
      .catch((reason: unknown) => {
        if (!alive || run !== runRef.current) return;
        setError(reason instanceof Error ? reason.message : t('pptx.preview.13') + '。');
        setStatus('error');
      });
    return () => { alive = false; };
  }, [file.file_id, file.mtime_ns, file.path, nonce, projectId]);

  useEffect(() => () => {
    if (!model) return;
    for (const url of model.objectUrls) URL.revokeObjectURL(url);
  }, [model]);

  if (status === 'loading') return <PreviewLoading label={t('pptx.preview.08') + '…'} />;
  if (status === 'error' || !model) {
    return <PreviewError message={error || t('pptx.preview.13') + '。'} onRetry={() => setNonce((value) => value + 1)} />;
  }

  return (
    <div className="pv-pptx">
      {model.orderFallback && (
        <div className="pv-pptx-note" role="note">{t('pptx.preview.07')}。</div>
      )}
      {model.omittedSlides > 0 && (
        <div className="pv-pptx-note" role="note">
          {t('pptx.preview.slidesOmitted', { n: MAX_SLIDES, m: model.omittedSlides })}
        </div>
      )}
      {model.slides.map((slide, slideIndex) => (
        <article className="pv-slide-card" key={slideIndex}>
          <div className="pv-slide-number">{t('pptx.preview.pageNumber', { n: slideIndex + 1 })}</div>
          <div
            className="pv-slide-canvas"
            style={{ aspectRatio: `${model.size.cx} / ${model.size.cy}` }}
            aria-label={t('pptx.preview.slideAria', { n: slideIndex + 1 })}
          >
            {slide.elements.map((element, elementIndex) => {
              const style: React.CSSProperties = {
                left: percent(element.box.x, model.size.cx),
                top: percent(element.box.y, model.size.cy),
                width: percent(element.box.cx, model.size.cx),
                height: percent(element.box.cy, model.size.cy),
                transform: element.box.rotation ? `rotate(${element.box.rotation}deg)` : undefined,
              };
              if (element.kind === 'image') {
                return (
                  <img
                    key={elementIndex}
                    className="pv-slide-image"
                    style={style}
                    src={element.url}
                    alt={element.alt}
                    draggable={false}
                  />
                );
              }
              return (
                <div
                  key={elementIndex}
                  className="pv-slide-text"
                  style={{
                    ...style,
                    background: element.fill,
                    borderColor: element.border,
                    justifyContent: element.vertical,
                  }}
                >
                  {element.paragraphs.map((paragraph, paragraphIndex) => (
                    <p key={paragraphIndex} style={{ textAlign: paragraph.align }}>
                      {paragraph.bullet && <span className="pv-slide-bullet">{paragraph.bullet} </span>}
                      {paragraph.runs.map((run, runIndex) => renderTextRun(run, runIndex, model.size))}
                    </p>
                  ))}
                </div>
              );
            })}
            {slide.degraded.length > 0 && (
              <div className="pv-slide-degraded" role="note" title={slide.degraded.join('；')}>
                {t('pptx.preview.simplified')}
              </div>
            )}
          </div>
          {slide.degraded.length > 0 && (
            <div className="pv-slide-degraded-detail">{slide.degraded.join('；')}</div>
          )}
        </article>
      ))}
    </div>
  );
};

export default PptxPreview;
