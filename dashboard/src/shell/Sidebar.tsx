import {
  CalendarDays,
  Home,
  LayoutGrid,
  type LucideIcon,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
} from 'lucide-react';
import { useState } from 'react';
import { Dot } from '../components/Status';
import { Link, usePath } from '../lib/router';
import { needsAttention } from '../lib/services';
import type { ThemePreference } from '../lib/theme';
import { useDashboard } from '../state/dashboard';
import { UserMenu } from './UserMenu';

export const NAV: { label: string; to: string; icon: LucideIcon; match: (path: string) => boolean }[] = [
  { label: 'Home', to: '/', icon: Home, match: (path) => path === '/' },
  { label: 'Apps', to: '/apps', icon: LayoutGrid, match: (path) => path.startsWith('/apps') },
  { label: 'Calendar', to: '/calendar', icon: CalendarDays, match: (path) => path === '/calendar' },
  { label: 'Chat', to: '/chat', icon: MessageSquare, match: (path) => path.startsWith('/chat') },
  { label: 'Settings', to: '/settings/ai', icon: Settings, match: (path) => path.startsWith('/settings') },
];

const COLLAPSED_KEY = 'mu3lab.sidebarCollapsed';

/**
 * Whether the menu is folded to icons: as the person last left it (remembered on this browser),
 * except on Chat, where it folds by itself so Open WebUI's own menu has room. Opening it on
 * Chat lasts until they leave Chat.
 */
export function useSidebarCollapsed(path: string) {
  const [saved, setSaved] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSED_KEY) === '1';
    } catch {
      return false;
    }
  });
  const onChat = path.startsWith('/chat');
  const [openOnChat, setOpenOnChat] = useState(false);
  // Leaving or entering Chat forgets an opening made there.
  const [wasOnChat, setWasOnChat] = useState(onChat);
  if (wasOnChat !== onChat) {
    setWasOnChat(onChat);
    setOpenOnChat(false);
  }
  const collapsed = onChat ? !openOnChat : saved;
  const toggle = () => {
    if (onChat) {
      setOpenOnChat(collapsed);
      return;
    }
    setSaved(!saved);
    try {
      localStorage.setItem(COLLAPSED_KEY, saved ? '0' : '1');
    } catch {
      // Storage can be unavailable; the choice lasts until the page reloads.
    }
  };
  return [collapsed, toggle] as const;
}

export function Sidebar({
  openSearch,
  theme,
  setTheme,
  collapsed,
  toggle,
}: {
  openSearch: () => void;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
  collapsed: boolean;
  toggle: () => void;
}) {
  const path = usePath();
  const { data } = useDashboard();
  const issues = data.services.services.filter(needsAttention).length;
  // Folded, each label stays for screen readers and shows on hover.
  const label = (text: string) => <span className={collapsed ? 'sr-only' : undefined}>{text}</span>;
  const hint = (text: string) => (collapsed ? text : undefined);
  const health = issues ? `${issues} app${issues === 1 ? '' : 's'} need attention` : 'All systems normal';
  return (
    <aside className={`sidebar${collapsed ? ' is-collapsed' : ''}`}>
      <div className="sidebar-top">
        <Link to="/" className="brand" title={hint('Home')}>
          <span className="brand-mark" aria-hidden={collapsed}>
            μ
          </span>
          {collapsed ? (
            <span className="sr-only">Mu3Lab</span>
          ) : (
            <span className="brand-text">
              <b>Mu3Lab</b>
              <small>{data.system.tailnet_dns_name || 'Private cloud'}</small>
            </span>
          )}
        </Link>
        <button
          type="button"
          className="btn btn-ghost btn-sm btn-icon sidebar-toggle"
          aria-label={collapsed ? 'Expand menu' : 'Collapse menu'}
          aria-expanded={!collapsed}
          title={collapsed ? 'Expand menu' : 'Collapse menu'}
          onClick={toggle}
        >
          {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
        </button>
      </div>
      <button type="button" className="search-trigger" onClick={openSearch} title={hint('Search')}>
        <Search />
        {label('Search')}
        {!collapsed && <kbd>{navigator.platform?.toLowerCase().includes('mac') ? '⌘K' : 'Ctrl K'}</kbd>}
      </button>
      <nav className="nav" aria-label="Main">
        {NAV.map(({ label: name, to, icon: Icon, match }) => (
          <Link
            key={to}
            to={to}
            className={match(path) ? 'active' : ''}
            aria-current={match(path) ? 'page' : undefined}
            title={hint(name)}
          >
            <Icon />
            {label(name)}
            {name === 'Apps' && issues > 0 && (
              <span className="nav-count" aria-label={`${issues} need attention`}>
                {issues}
              </span>
            )}
          </Link>
        ))}
      </nav>
      <div className="sidebar-footer">
        <Link to="/settings/system" className="system-pill" title={hint(health)}>
          <Dot tone={issues ? 'amber' : 'green'} />
          {label(health)}
        </Link>
        <UserMenu theme={theme} setTheme={setTheme} compact={collapsed} />
      </div>
    </aside>
  );
}

export function MobileBar({
  openSearch,
  theme,
  setTheme,
}: {
  openSearch: () => void;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
}) {
  const path = usePath();
  return (
    <>
      <header className="mobile-top">
        <Link to="/" className="brand">
          <span className="brand-mark">μ</span>
          <b>Mu3Lab</b>
        </Link>
        <span className="mobile-top-actions">
          <button type="button" className="btn btn-ghost btn-md btn-icon" aria-label="Search" onClick={openSearch}>
            <Search />
          </button>
          <UserMenu theme={theme} setTheme={setTheme} compact />
        </span>
      </header>
      <nav className="mobile-tabs" aria-label="Main">
        {NAV.map(({ label, to, icon: Icon, match }) => (
          <Link
            key={to}
            to={to}
            className={match(path) ? 'active' : ''}
            aria-current={match(path) ? 'page' : undefined}
          >
            <Icon />
            <span>{label}</span>
          </Link>
        ))}
      </nav>
    </>
  );
}
