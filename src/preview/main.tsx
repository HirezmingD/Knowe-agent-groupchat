/** 独立预览 BrowserWindow 的 React 入口。 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import i18n from '../i18n';
import PreviewApp from './PreviewApp';
import '../styles/knowe-tokens.css';
import '../styles/knowe-components.css';
import '../styles/knowe-preview.css';
import '../styles/knowe-preview-window.css';
import '../styles/knowe-v0452-code-preview.css';

const root = document.getElementById('preview-root');
if (!root) throw new Error(i18n.t('main.02'));

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <PreviewApp />
  </React.StrictMode>,
);
