import {
  ChevronsUpDown,
  CircleArrowUp,
  GitBranch,
  LayoutGrid,
  LogOut,
  type LucideIcon,
  Monitor,
  Moon,
  Power,
  Rocket,
  RotateCw,
  ScrollText,
  Settings,
  Sparkles,
  Sun,
} from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { api, type Mu3LabUpdate, type OkResponse, postJsonApi } from '../api';
import { useConfirm } from '../components/Dialog';
import { RELEASES_URL } from '../features/settings/WhatsNew';
import { navigate } from '../lib/router';
import type { ThemePreference } from '../lib/theme';
import { useAction } from '../lib/useAction';
import { useApi } from '../lib/useApi';
import { useDashboard, useIsAdmin } from '../state/dashboard';
import { showTurningOff } from './PowerOverlay';

const REPOSITORY_URL = 'https://github.com/dcazes/Mu3Lab';
// Authentik's outpost ends the dashboard session and sends the person to its sign-out page.
const SIGN_OUT_URL = '/outpost.goauthentik.io/sign_out';

const THEMES: { value: ThemePreference; label: string; icon: LucideIcon }[] = [
  { value: 'system', label: 'Match system', icon: Monitor },
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
];

interface Item {
  label: string;
  icon: LucideIcon;
  run: () => void;
  badge?: boolean;
  danger?: boolean;
}

/** The person's name opens everything about Mu3Lab itself: settings, updates, releases and signing out. */
export function UserMenu({
  theme,
  setTheme,
  compact = false,
}: {
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
  compact?: boolean;
}) {
  const { data } = useDashboard();
  const admin = useIsAdmin();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const id = useId();
  // Only administrators can update, so only they are told an update is waiting.
  const update = useApi<Mu3LabUpdate>(admin ? '/api/v1/system/update' : null).data;
  const user = data.identity.display_name || data.identity.username || 'Account';
  const confirm = useConfirm();
  const { run } = useAction();
  const restart = async () => {
    // Asked again now: the dashboard's code may have changed since this page loaded.
    const rebuild = await api<Mu3LabUpdate>('/api/v1/system/update').then(
      (current) => Boolean(current.dashboard_outdated),
      () => Boolean(update?.dashboard_outdated),
    );
    if (
      !(await confirm({
        title: 'Restart Mu3Lab?',
        description: rebuild
          ? 'The dashboard’s code has changed since its page was built, so the page is rebuilt first. Then the dashboard and its background worker restart. Your apps keep running. The dashboard is back in about a minute.'
          : 'The dashboard and its background worker restart, which clears anything stuck in them. Your apps keep running. The dashboard is back in a few seconds.',
        confirmLabel: rebuild ? 'Rebuild and restart' : 'Restart Mu3Lab',
      }))
    )
      return;
    await run(
      'restart',
      () => postJsonApi<OkResponse>('/api/v1/system/restart', {}),
      (result) => result.message ?? '',
    );
  };
  const turnOff = async () => {
    if (
      !(await confirm({
        title: 'Turn off Mu3Lab?',
        description:
          'Every app, the chat assistants, sign-in and this dashboard stop, for everyone in the household. Only a small page stays on at this address, with a button to turn Mu3Lab back on. Turning off takes about a minute and turning on a few minutes. A backup that falls due while it is off runs once it is back on.',
        confirmLabel: 'Turn off',
        tone: 'danger',
      }))
    )
      return;
    const result = await run('power-off', () => postJsonApi<OkResponse>('/api/v1/power/off', {}));
    if (result?.ok) showTurningOff();
  };
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !root.current?.contains(event.target as Node))
        setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);
  const go = (path: string) => () => navigate(path);
  const external = (url: string) => () => window.open(url, '_blank', 'noopener,noreferrer');
  const groups: Item[][] = [
    [
      { label: 'Settings', icon: Settings, run: go('/settings/ai') },
      { label: 'Get started', icon: Rocket, run: go('/settings/get-started') },
      { label: 'Apps', icon: LayoutGrid, run: go('/apps') },
      ...(admin
        ? [
            {
              label: update?.available ? 'Update available' : 'Update',
              icon: CircleArrowUp,
              run: go('/settings/system'),
              badge: Boolean(update?.available),
            },
            { label: 'What’s new', icon: Sparkles, run: go('/settings/whats-new') },
            { label: 'Restart Mu3Lab', icon: RotateCw, run: () => void restart() },
            { label: 'Turn off Mu3Lab', icon: Power, run: () => void turnOff() },
          ]
        : []),
    ],
    [
      { label: 'Changelog', icon: ScrollText, run: external(RELEASES_URL) },
      { label: 'GitHub', icon: GitBranch, run: external(REPOSITORY_URL) },
    ],
    [{ label: 'Log out', icon: LogOut, run: () => window.location.assign(SIGN_OUT_URL), danger: true }],
  ];
  return (
    <div className={`user-menu${compact ? ' is-compact' : ''}`} ref={root}>
      <button
        type="button"
        className="user-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={id}
        aria-label={compact ? `${user} menu` : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="avatar" aria-hidden="true">
          {user.slice(0, 1).toUpperCase()}
        </span>
        {!compact && (
          <>
            <span className="sidebar-user-name">{user}</span>
            {update?.available && <span className="user-menu-dot" aria-label="Update available" />}
            <ChevronsUpDown className="user-menu-chevron" aria-hidden="true" />
          </>
        )}
      </button>
      {open && (
        <div className="menu-list user-menu-list" role="menu" id={id} aria-label={`${user} menu`}>
          {groups.map((items, index) => (
            <div key={index} className="user-menu-group" role="group">
              {items.map(({ label, icon: Icon, run, badge, danger }) => (
                <button
                  key={label}
                  type="button"
                  role="menuitem"
                  className={danger ? 'danger' : ''}
                  onClick={() => {
                    setOpen(false);
                    run();
                  }}
                >
                  <Icon />
                  {label}
                  {badge && <span className="user-menu-dot" aria-hidden="true" />}
                </button>
              ))}
            </div>
          ))}
          <div className="user-menu-theme" role="radiogroup" aria-label="Theme">
            {THEMES.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={theme === value}
                aria-label={label}
                title={label}
                onClick={() => setTheme(value)}
              >
                <Icon />
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
