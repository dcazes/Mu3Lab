import { type AnchorHTMLAttributes, type MouseEvent, useSyncExternalStore } from 'react';

const REDIRECTS: Record<string, string> = {
  '/connections': '/settings/ai',
  '/connections/providers': '/settings/ai',
  '/connections/mcp': '/settings/integrations',
  '/security': '/settings/security',
  '/security/identity': '/settings/security',
  '/security/backups': '/settings/security',
  '/system': '/settings/system',
  '/system/diagnostics': '/settings/system',
  '/settings': '/settings/ai',
};

function subscribe(onChange: () => void) {
  window.addEventListener('popstate', onChange);
  return () => window.removeEventListener('popstate', onChange);
}

export function navigate(path: string, { replace = false } = {}) {
  if (replace) window.history.replaceState({}, '', path);
  else window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
  window.scrollTo({ top: 0 });
}

export function resolvePath(path: string): string {
  const trimmed = path.length > 1 ? path.replace(/\/+$/, '') : path;
  return REDIRECTS[trimmed] || trimmed;
}

export function usePath(): string {
  const path = useSyncExternalStore(subscribe, () => window.location.pathname);
  return resolvePath(path);
}

export function Link({ to, onClick, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }) {
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    navigate(to);
  };
  return <a href={to} onClick={handle} {...props} />;
}
