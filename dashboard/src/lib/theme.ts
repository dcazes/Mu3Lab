import { useEffect, useSyncExternalStore } from 'react';

export type ThemePreference = 'system' | 'light' | 'dark';
/** How the Apps page draws its sections: each in a frame, or open on the page. */
export type SectionStyle = 'framed' | 'open';
/** Whether every Apps section takes a full row (the default), or small ones share a row. */
export type SectionWidth = 'full' | 'compact';

const STORAGE_KEY = 'mu3lab.theme';
const SECTIONS_KEY = 'mu3lab.sections';
const WIDTH_KEY = 'mu3lab.sectionWidth';
const media = () => window.matchMedia?.('(prefers-color-scheme: dark)');

function read(key: string) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* Private browsing: the choice lasts for this session only. */
  }
}

export function storedPreference(): ThemePreference {
  const value = read(STORAGE_KEY);
  return value === 'light' || value === 'dark' ? value : 'system';
}

export function storedSectionStyle(): SectionStyle {
  return read(SECTIONS_KEY) === 'open' ? 'open' : 'framed';
}

export function storedSectionWidth(): SectionWidth {
  return read(WIDTH_KEY) === 'compact' ? 'compact' : 'full';
}

export function applyTheme(preference: ThemePreference) {
  const dark = preference === 'dark' || (preference === 'system' && Boolean(media()?.matches));
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#0d0e11' : '#fbfbfc');
}

export function applySectionStyle(style: SectionStyle) {
  document.documentElement.dataset.sections = style;
}

// One copy of each choice for the whole page, so the sidebar, the command palette and
// Settings > Appearance always agree.
let theme = storedPreference();
let sections = storedSectionStyle();
let width = storedSectionWidth();
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const changed = () => listeners.forEach((listener) => listener());

function setTheme(preference: ThemePreference) {
  theme = preference;
  write(STORAGE_KEY, preference === 'system' ? null : preference);
  applyTheme(preference);
  changed();
}

function setSectionStyle(style: SectionStyle) {
  sections = style;
  write(SECTIONS_KEY, style === 'framed' ? null : style);
  applySectionStyle(style);
  changed();
}

function setSectionWidth(value: SectionWidth) {
  width = value;
  write(WIDTH_KEY, value === 'full' ? null : value);
  changed();
}

export function useTheme() {
  const preference = useSyncExternalStore(subscribe, () => theme);
  useEffect(() => {
    if (preference !== 'system') return;
    const query = media();
    const follow = () => applyTheme('system');
    query?.addEventListener('change', follow);
    return () => query?.removeEventListener('change', follow);
  }, [preference]);
  return { preference, setPreference: setTheme };
}

export function useSectionStyle() {
  const style = useSyncExternalStore(subscribe, () => sections);
  return { style, setStyle: setSectionStyle };
}

export function useSectionWidth() {
  const value = useSyncExternalStore(subscribe, () => width);
  return { width: value, setWidth: setSectionWidth };
}
