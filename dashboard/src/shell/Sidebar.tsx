import { Home, LayoutGrid, type LucideIcon, MessageSquare, Monitor, Moon, Search, Settings, Sun } from 'lucide-react';
import { Dot } from '../components/Status';
import { Link, usePath } from '../lib/router';
import { needsAttention } from '../lib/services';
import type { ThemePreference } from '../lib/theme';
import { useDashboard } from '../state/dashboard';

export const NAV: { label: string; to: string; icon: LucideIcon; match: (path: string) => boolean }[] = [
  { label: 'Home', to: '/', icon: Home, match: (path) => path === '/' || path === '/calendar' },
  { label: 'Apps', to: '/apps', icon: LayoutGrid, match: (path) => path.startsWith('/apps') },
  { label: 'Chat', to: '/chat', icon: MessageSquare, match: (path) => path.startsWith('/chat') },
  { label: 'Settings', to: '/settings/ai', icon: Settings, match: (path) => path.startsWith('/settings') },
];

const THEME_ORDER: ThemePreference[] = ['system', 'light', 'dark'];
const THEME_ICON = { system: Monitor, light: Sun, dark: Moon };
const THEME_LABEL = { system: 'Match system', light: 'Light', dark: 'Dark' };

export function Sidebar({
  openSearch,
  theme,
  setTheme,
}: {
  openSearch: () => void;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
}) {
  const path = usePath();
  const { data } = useDashboard();
  const issues = data.services.services.filter(needsAttention).length;
  const ThemeIcon = THEME_ICON[theme];
  const nextTheme = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length];
  const user = data.identity.display_name || data.identity.username || '';
  return (
    <aside className="sidebar">
      <Link to="/" className="brand">
        <span className="brand-mark">μ</span>
        <span className="brand-text">
          <b>Mu3Lab</b>
          <small>{data.system.tailnet_dns_name || 'Private cloud'}</small>
        </span>
      </Link>
      <button type="button" className="search-trigger" onClick={openSearch}>
        <Search />
        <span>Search</span>
        <kbd>{navigator.platform?.toLowerCase().includes('mac') ? '⌘K' : 'Ctrl K'}</kbd>
      </button>
      <nav className="nav" aria-label="Main">
        {NAV.map(({ label, to, icon: Icon, match }) => (
          <Link
            key={to}
            to={to}
            className={match(path) ? 'active' : ''}
            aria-current={match(path) ? 'page' : undefined}
          >
            <Icon />
            <span>{label}</span>
            {label === 'Apps' && issues > 0 && (
              <span className="nav-count" aria-label={`${issues} need attention`}>
                {issues}
              </span>
            )}
          </Link>
        ))}
      </nav>
      <div className="sidebar-footer">
        <Link to="/settings/system" className="system-pill">
          <Dot tone={issues ? 'amber' : 'green'} />
          {issues ? `${issues} app${issues === 1 ? '' : 's'} need attention` : 'All systems normal'}
        </Link>
        <div className="sidebar-user">
          {user && (
            <>
              <span className="avatar" aria-hidden="true">
                {user.slice(0, 1).toUpperCase()}
              </span>
              <span className="sidebar-user-name">{user}</span>
            </>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-sm btn-icon theme-toggle"
            onClick={() => setTheme(nextTheme)}
            aria-label={`Theme: ${THEME_LABEL[theme]}. Switch to ${THEME_LABEL[nextTheme]}`}
            title={`Theme: ${THEME_LABEL[theme]}`}
          >
            <ThemeIcon />
          </button>
        </div>
      </div>
    </aside>
  );
}

export function MobileBar({ openSearch }: { openSearch: () => void }) {
  const path = usePath();
  return (
    <>
      <header className="mobile-top">
        <Link to="/" className="brand">
          <span className="brand-mark">μ</span>
          <b>Mu3Lab</b>
        </Link>
        <button type="button" className="btn btn-ghost btn-md btn-icon" aria-label="Search" onClick={openSearch}>
          <Search />
        </button>
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
