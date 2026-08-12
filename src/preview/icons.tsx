/** 独立预览窗口与主窗口文件卡片共用的轻量 SVG 图标。 */

import React from 'react';
import type { PreviewKind } from './fileKinds';

type IconProps = { size?: number };

const svgProps = (size = 16): React.SVGProps<SVGSVGElement> => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none',
  'aria-hidden': true,
});

const stroke = {
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

const FileFrame: React.FC<IconProps & { children?: React.ReactNode }> = ({ size, children }) => (
  <svg {...svgProps(size)}>
    <path d="M6 3h7l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" {...stroke} />
    <path d="M13 3v5h5" {...stroke} />
    {children}
  </svg>
);

const MarkdownIcon: React.FC<IconProps> = ({ size }) => (
  <FileFrame size={size}>
    <path d="M8 16v-4l2 2 2-2v4M15.5 12v4M15.5 16l-1.3-1.4M15.5 16l1.3-1.4" {...stroke} strokeWidth={1.45} />
  </FileFrame>
);
const HtmlIcon: React.FC<IconProps> = ({ size }) => (
  <FileFrame size={size}>
    <path d="M9.5 12.5 8 14.5l1.5 2M14.5 12.5 16 14.5l-1.5 2" {...stroke} strokeWidth={1.45} />
  </FileFrame>
);
const ImageIcon: React.FC<IconProps> = ({ size }) => (
  <FileFrame size={size}>
    <circle cx="9.5" cy="13" r="1.1" {...stroke} strokeWidth={1.4} />
    <path d="M7 18l3-3 2 2 2.5-2.5L18 18" {...stroke} strokeWidth={1.4} />
  </FileFrame>
);
const PdfIcon: React.FC<IconProps> = ({ size }) => (
  <FileFrame size={size}>
    <path d="M8 12.5h1.4a1 1 0 0 1 0 2H8v-2ZM8 14.5V17M12 12.5v4.5M12 12.5h1.2a1.3 1.3 0 0 1 0 4.5H12M16 12.5h1.6M16 12.5V17M16 14.7h1.3" {...stroke} strokeWidth={1.15} />
  </FileFrame>
);
const DocIcon: React.FC<IconProps> = ({ size }) => (
  <FileFrame size={size}><path d="M8 12.5h8M8 15h8M8 17.5h5" {...stroke} strokeWidth={1.4} /></FileFrame>
);
const SlidesIcon: React.FC<IconProps> = ({ size }) => (
  <FileFrame size={size}><rect x="8" y="12.5" width="8" height="5" rx="1" {...stroke} strokeWidth={1.4} /></FileFrame>
);
const SheetIcon: React.FC<IconProps> = ({ size }) => (
  <FileFrame size={size}><path d="M8 12.5h8v5H8zM8 15h8M11.5 12.5v5" {...stroke} strokeWidth={1.3} /></FileFrame>
);
const CodeIcon: React.FC<IconProps> = ({ size }) => (
  <FileFrame size={size}><path d="m10 12-2 2.5 2 2.5M14 12l2 2.5-2 2.5" {...stroke} strokeWidth={1.45} /></FileFrame>
);
const TextIcon: React.FC<IconProps> = ({ size }) => (
  <FileFrame size={size}><path d="M8 12.5h8M8 15h8M8 17.5h8" {...stroke} strokeWidth={1.35} /></FileFrame>
);
const GenericFileIcon: React.FC<IconProps> = ({ size }) => <FileFrame size={size} />;

export const IconForKind: React.FC<{ kind: PreviewKind; size?: number }> = ({ kind, size }) => {
  switch (kind) {
    case 'markdown': return <MarkdownIcon size={size} />;
    case 'html': return <HtmlIcon size={size} />;
    case 'image': return <ImageIcon size={size} />;
    case 'pdf': return <PdfIcon size={size} />;
    case 'docx': return <DocIcon size={size} />;
    case 'pptx': return <SlidesIcon size={size} />;
    case 'sheet': return <SheetIcon size={size} />;
    case 'code': return <CodeIcon size={size} />;
    case 'text': return <TextIcon size={size} />;
    default: return <GenericFileIcon size={size} />;
  }
};

export const IconClose: React.FC<IconProps> = ({ size }) => (
  <svg {...svgProps(size)}><path d="M6 6l12 12M18 6 6 18" {...stroke} /></svg>
);
export const IconZoomIn: React.FC<IconProps> = ({ size }) => (
  <svg {...svgProps(size)}><circle cx="11" cy="11" r="6" {...stroke} /><path d="M11 8.5v5M8.5 11h5M20 20l-4.3-4.3" {...stroke} /></svg>
);
export const IconZoomOut: React.FC<IconProps> = ({ size }) => (
  <svg {...svgProps(size)}><circle cx="11" cy="11" r="6" {...stroke} /><path d="M8.5 11h5M20 20l-4.3-4.3" {...stroke} /></svg>
);
export const IconFit: React.FC<IconProps> = ({ size }) => (
  <svg {...svgProps(size)}><path d="M4 9V5a1 1 0 0 1 1-1h4M20 9V5a1 1 0 0 0-1-1h-4M4 15v4a1 1 0 0 0 1 1h4M20 15v4a1 1 0 0 1-1 1h-4" {...stroke} /></svg>
);
export const IconRetry: React.FC<IconProps> = ({ size }) => (
  <svg {...svgProps(size)}><path d="M4 12a8 8 0 1 1 2.3 5.6M4 12V8M4 12h4" {...stroke} /></svg>
);
export const FolderRevealIcon: React.FC<IconProps> = ({ size }) => (
  <svg {...svgProps(size)}>
    <path d="M3.5 7.5h6l1.7 2H20a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8.5a1 1 0 0 1 .5-1Z" {...stroke} />
    <path d="m13.5 13.5 2 2 3.5-3.5" {...stroke} />
  </svg>
);
