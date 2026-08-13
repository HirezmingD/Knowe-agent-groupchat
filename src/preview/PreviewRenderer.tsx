/** 文件类型到具体渲染器的唯一纯 switch；重型依赖通过 React.lazy 分块加载。 */

import React, { Suspense, lazy, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import type { PreviewFilePayload } from '../shared/bridge';
import { extOf, kindOf } from './fileKinds';
import CodePreview from './renderers/CodePreview';
import HtmlPreview from './renderers/HtmlPreview';
import ImagePreview from './renderers/ImagePreview';
import TextPreview from './renderers/TextPreview';
import FallbackPreview from './renderers/FallbackPreview';
import { PreviewLoading } from './renderers/PreviewStates';

const PdfPreview = lazy(() => import('./renderers/PdfPreview'));
const MarkdownPreview = lazy(() => import('./renderers/MarkdownPreview'));
const SheetPreview = lazy(() => import('./renderers/SheetPreview'));
const DocxPreview = lazy(() => import('./renderers/DocxPreview'));
const PptxPreview = lazy(() => import('./renderers/PptxPreview'));

export interface PreviewRendererProps {
  tabKey: string;
  file: PreviewFilePayload;
  projectId: string;
  /** null = no navigation request; empty string = reveal document top. */
  fragment: string | null;
  /** Monotonic token so clicking the same anchor again still retriggers navigation. */
  fragmentRequest: number;
  onMounted: (key: string) => void;
  onOpenRelative: (href: string) => void | Promise<void>;
}

const PreviewRenderer: React.FC<PreviewRendererProps> = ({
  tabKey,
  file,
  projectId,
  fragment,
  fragmentRequest,
  onMounted,
  onOpenRelative,
}) => {
  const { t } = useTranslation();
  useEffect(() => {
    onMounted(tabKey);
  }, [onMounted, tabKey]);

  const props = { file, projectId };
  let content: React.ReactNode;
  switch (kindOf(file)) {
    case 'html': content = <HtmlPreview {...props} />; break;
    case 'image': content = <ImagePreview {...props} />; break;
    case 'pdf': content = <PdfPreview {...props} />; break;
    case 'docx': content = <DocxPreview {...props} />; break;
    case 'sheet': content = <SheetPreview {...props} />; break;
    case 'pptx': content = <PptxPreview {...props} />; break;
    case 'code': content = <CodePreview {...props} />; break;
    case 'markdown': content = (
      <MarkdownPreview
        {...props}
        fragment={fragment}
        fragmentRequest={fragmentRequest}
        onOpenRelative={onOpenRelative}
      />
    ); break;
    case 'text': content = <TextPreview {...props} />; break;
    default: {
      const ext = (file.ext || extOf(file.name || file.path)).replace(/^\./, '').toLowerCase();
      const reason = ext === 'xls' ? t('sheet.preview.legacyXls') : undefined;
      content = <FallbackPreview {...props} reason={reason} />;
    }
  }

  return <Suspense fallback={<PreviewLoading />}>{content}</Suspense>;
};

export default PreviewRenderer;
