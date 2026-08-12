/**
 * main.tsx — React 入口
 *
 * 走廊页已降级为 Ctrl+Shift+D 抽屉（见 components/DevDrawer.tsx），
 * 不再走 /corridor 路由，所以这里只挂 App。
 */

import ReactDOM from 'react-dom/client';
import './styles/knowe-tokens.css';
import './styles/knowe-components.css';
import './styles/bubble-reasoning.css';
import App from './app/App';
import i18n from './i18n';

// [v1.0.19.4] 全局兜底：窗口任意处的拖拽默认行为（浏览器直接打开/导航到被拖入的文件）
//   会把整个单页应用顶掉。这里统一拦下 dragover/drop 的默认动作；真正要接收文件的
//   聊天区在自己的 onDrop 里处理，drop 到别处则被安全丢弃（既不导航，也不误加文件）。
window.addEventListener('dragover', (e) => { e.preventDefault(); }, false);
window.addEventListener('drop', (e) => { e.preventDefault(); }, false);

const root = document.getElementById('root');
if (!root) {
  throw new Error(i18n.t('main.01'));
}

ReactDOM.createRoot(root).render(<App />);
