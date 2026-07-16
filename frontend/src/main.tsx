import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@picocss/pico/css/pico.min.css';
import { App } from './App';
import './index.css';

const rootElement = document.getElementById('root');
if (!rootElement) {
  // 19原則(b): フォールバック禁止。HTML 側の root が無いのは構成ミスなので fail-fast する。
  throw new Error('Root element #root not found in index.html');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
