import { AlertTriangle, ArrowRight, DatabaseBackup, Loader2, Plus, X } from 'lucide-react';
import { useState } from 'react';
import type { Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Dot } from '../../components/Status';
import { Link } from '../../lib/router';
import {
  isEverydayApp,
  isInstalled,
  isRunning,
  isWorking,
  launchTarget,
  needsAttention,
  stateLabel,
  stateTone,
} from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { AppQuickPanel } from './AppQuickPanel';
import { HomeCalendar } from './HomeCalendar';
import { GetStarted } from './GetStarted';
import { SystemStatus } from './SystemStatus';

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

/** A tile opens the app's quick panel; the panel's Open button opens the app itself. */
function LauncherTile({ service, onOpen }: { service: Service; onOpen: () => void }) {
  const tone = stateTone(service.display_state);
  return (
    <button
      type="button"
      className={`tile${launchTarget(service) ? '' : ' tile-muted'}`}
      aria-label={`${service.name}: ${stateLabel[service.display_state]}`}
      aria-haspopup="dialog"
      onClick={onOpen}
    >
      <AppIcon id={service.id} size="lg" />
      <span className="tile-name">{service.name}</span>
      {!isRunning(service) && (
        <span className="tile-state">
          <Dot tone={tone} />
          {stateLabel[service.display_state]}
        </span>
      )}
    </button>
  );
}

// Reminders the owner may wave away for good on this browser; the rest stay until fixed.
const DISMISSIBLE = new Set(['backup-service-missing']);
const DISMISSED_KEY = 'mu3lab.dismissedAttention';

function useDismissed() {
  const [dismissed, setDismissed] = useState<string[]>(() => {
    try {
      const value: unknown = JSON.parse(localStorage.getItem(DISMISSED_KEY) || '[]');
      return Array.isArray(value) ? value.filter((key): key is string => typeof key === 'string') : [];
    } catch {
      return [];
    }
  });
  const dismiss = (key: string) => {
    const next = [...dismissed, key];
    setDismissed(next);
    try {
      localStorage.setItem(DISMISSED_KEY, JSON.stringify(next));
    } catch {
      // Storage can be unavailable; the reminder is hidden until the page reloads.
    }
  };
  return [dismissed, dismiss] as const;
}

function Attention() {
  const { data } = useDashboard();
  const [dismissed, dismiss] = useDismissed();
  const failing = data.services.services.filter(needsAttention);
  const working = data.services.services.filter(isWorking);
  const setupIncomplete = data.provisioning.available && !data.provisioning.complete;
  const setupFailed = setupIncomplete && (data.provisioning.blocked || data.core.current_job?.state === 'failed');
  const items = [
    // The button that tries again sits in System settings.
    ...(setupFailed
      ? [
          {
            key: 'setup-failed',
            icon: AlertTriangle,
            tone: 'warning',
            text: 'A setup step didn’t finish. Open System settings to try again.',
            to: '/settings/system',
          },
        ]
      : []),
    ...failing.map((service) => ({
      key: service.id,
      icon: AlertTriangle,
      tone: 'warning',
      text: `${service.name}: ${stateLabel[service.display_state].toLowerCase()}`,
      to: `/apps/${service.id}`,
    })),
    // Sent to administrators only, and only while backups fall behind the schedule.
    ...(setupIncomplete ? [] : data.backup_attention || [])
      .filter((item) => !dismissed.includes(item.key))
      .map((item) => ({
        key: item.key,
        icon: DatabaseBackup,
        tone: 'warning',
        text: item.message,
        to: '/settings/security',
      })),
    ...working.map((service) => ({
      key: `working-${service.id}`,
      icon: Loader2,
      tone: 'progress',
      text: `${service.name} is ${stateLabel[service.display_state].toLowerCase()}…`,
      to: `/apps/${service.id}`,
    })),
  ];
  if (!items.length) return null;
  return (
    <section className="attention" aria-label="Needs attention">
      {items.map(({ key, icon: Icon, tone, text, to }) => (
        <div key={key} className="attention-row">
          <Link to={to} className={`attention-item attention-${tone}`}>
            <Icon className={tone === 'progress' ? 'spin' : ''} />
            <span>{text}</span>
            <ArrowRight className="attention-arrow" />
          </Link>
          {DISMISSIBLE.has(key) && (
            <button
              type="button"
              className="btn btn-ghost btn-sm btn-icon"
              aria-label="Dismiss this reminder"
              onClick={() => dismiss(key)}
            >
              <X />
            </button>
          )}
        </div>
      ))}
    </section>
  );
}

export function HomePage() {
  const { data } = useDashboard();
  const services = data.services.services;
  const apps = services.filter((service) => isEverydayApp(service) && isInstalled(service));
  const name = (data.identity.display_name || data.identity.username || '').split(' ')[0];
  const [panel, setPanel] = useState('');
  // Look the app up each render so the panel follows its live state.
  const selected = services.find((service) => service.id === panel);
  return (
    <div className="home">
      <header className="home-header">
        <h1>
          {greeting()}
          {name ? `, ${name}` : ''}
        </h1>
        <p>{new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</p>
      </header>
      <SystemStatus />
      <Attention />
      <GetStarted />
      <section aria-labelledby="apps-heading">
        <div className="section-title">
          <h2 id="apps-heading">Your apps</h2>
          <Link to="/apps">Manage</Link>
        </div>
        <div className="launcher">
          {apps.map((service) => (
            <LauncherTile key={service.id} service={service} onOpen={() => setPanel(service.id)} />
          ))}
          <Link to="/apps/discover" className="tile tile-add" aria-label="Add apps">
            <span className="app-icon app-icon-lg">
              <Plus />
            </span>
            <span className="tile-name">Add apps</span>
          </Link>
        </div>
      </section>
      <HomeCalendar nextcloud={services.find((service) => service.id === 'nextcloud')} />
      {selected && <AppQuickPanel service={selected} onClose={() => setPanel('')} />}
    </div>
  );
}
