/**
 * Mu3Lab :: dashboard/src/main.tsx
 * WHAT: React entry point. Mounts <App/> on #root, nothing else.
 * WHY:  Kept trivial so future routing/state work has one obvious home.
 * DEBUG: Blank page? Check devtools console + that #root exists in index.html.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
