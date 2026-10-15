import '@fontsource-variable/inter';
import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import { applyTheme, storedPreference } from './lib/theme';

applyTheme(storedPreference());

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    // This build's own file name identifies the release, so each release gets a fresh shell cache
    // and the previous one is deleted (review R24).
    const release = new URL(import.meta.url).pathname.split('/').pop() ?? '';
    navigator.serviceWorker.register(`/sw.js?release=${encodeURIComponent(release)}`).catch(() => undefined);
  });
}

// A tab opened before a Mu3Lab update asks for page pieces the new release no longer has.
// Reload it once onto the new release instead of showing a broken page.
window.addEventListener('vite:preloadError', (event) => {
  const key = 'mu3lab-reloaded-for-update';
  try {
    if (sessionStorage.getItem(key) === location.pathname) return;
    sessionStorage.setItem(key, location.pathname);
  } catch {
    return; // without storage, a reload could repeat forever
  }
  event.preventDefault();
  location.reload();
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
