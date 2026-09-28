import { useEffect, useState } from 'react';

export type ThemePreference = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'mu3lab.theme';
const media = () => window.matchMedia?.('(prefers-color-scheme: dark)');

export function storedPreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

export function applyTheme(preference: ThemePreference) {
  const dark = preference === 'dark' || (preference === 'system' && Boolean(media()?.matches));
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#0d0e11' : '#fbfbfc');
}

export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(storedPreference);
  useEffect(() => {
    applyTheme(preference);
    try {
      if (preference === 'system') window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, preference);
    } catch {
      /* Private browsing: the choice lasts for this session only. */
    }
    if (preference !== 'system') return;
    const query = media();
    const follow = () => applyTheme('system');
    query?.addEventListener('change', follow);
    return () => query?.removeEventListener('change', follow);
  }, [preference]);
  return { preference, setPreference };
}
