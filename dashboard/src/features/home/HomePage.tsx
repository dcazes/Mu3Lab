import { AlertTriangle, ArrowRight, ChevronDown, DatabaseBackup, Loader2, X } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import type { DismissedResponse, Service } from '../../api';
import { postJsonApi } from '../../api/client';
import { AppIcon } from '../../components/AppIcon';
import { Dot } from '../../components/Status';
import { Link } from '../../lib/router';
import {
  appSections,
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
import { CoreSetupProgress, useCoreSetupActive } from './CoreSetupProgress';
import { GetStarted } from './GetStarted';
import { SystemStatus } from './SystemStatus';

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

const FOLDED_KEY = 'mu3lab.homeFolded';

/** Sections folded to a row of icons, remembered in this browser. */
function useFolded() {
  const [folded, setFolded] = useState<string[]>(() => {
    try {
      const value: unknown = JSON.parse(localStorage.getItem(FOLDED_KEY) || '[]');
      return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
    } catch {
      return [];
    }
  });
  const toggle = (id: string) => {
    const next = folded.includes(id) ? folded.filter((item) => item !== id) : [...folded, id];
    setFolded(next);
    try {
      localStorage.setItem(FOLDED_KEY, JSON.stringify(next));
    } catch {
      // Storage can be unavailable (private mode); the choice just won't persist.
    }
  };
  return [folded, toggle] as const;
}

/** A folded tile is the icon alone; its name is the tooltip. */
function MiniTile({ service, onOpen }: { service: Service; onOpen: () => void }) {
  return (
    <button
      type="button"
      className={`mini-tile${launchTarget(service) ? '' : ' tile-muted'}`}
      aria-label={`${service.name}: ${stateLabel[service.display_state]}`}
      title={service.name}
      aria-haspopup="dialog"
      onClick={onOpen}
    >
      <AppIcon id={service.id} />
      {!isRunning(service) && <Dot tone={stateTone(service.display_state)} />}
    </button>
  );
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

/**
 * Warnings the person dismissed, on all their devices. Each warning is named by what it says and
 * when its problem began, so a dismissal lasts only while that same problem continues.
 */
function useDismissed() {
  const { data, refresh } = useDashboard();
  // Hidden at once on a click, before the server has answered.
  const [pending, setPending] = useState<string[]>([]);
  const dismissed = [...(data.dismissed || []), ...pending];
  const dismiss = (warning: string) => {
    setPending((current) => [...current, warning]);
    postJsonApi<DismissedResponse>('/api/v1/dismissed', { warning })
      .then(() => refresh())
      .catch(() => {
        setPending((current) => current.filter((item) => item !== warning));
        toast.error('That warning could not be hidden. Try again.');
      });
  };
  return [dismissed, dismiss] as const;
}

function Attention() {
  const { data } = useDashboard();
  const [dismissed, dismiss] = useDismissed();
  const failing = data.services.services.filter(needsAttention);
  // The core apps' own progress card covers them while their setup runs.
  const coreActive = useCoreSetupActive();
  const coreApps = new Set(coreActive ? (data.core.apps || []).map((app) => app.service_id) : []);
  const working = data.services.services.filter((service) => isWorking(service) && !coreApps.has(service.id));
  const setupIncomplete = data.provisioning.available && !data.provisioning.complete;
  const setupFailed = setupIncomplete && (data.provisioning.blocked || data.core.current_job?.state === 'failed');
  const worker = data.system.worker_state || '';
  const workerDown = data.system.ok && !['', 'active', 'activating', 'unknown'].includes(worker);
  const warnings = [
    // Nothing runs without these two, so they lead.
    ...(data.system.ok && !data.system.docker_ready
      ? [
          {
            key: 'docker',
            warning: 'docker|down',
            icon: AlertTriangle,
            tone: 'warning',
            text: 'Docker isn’t reachable, so apps can’t start or run. Open System settings for details.',
            to: '/settings/system',
          },
        ]
      : []),
    ...(workerDown
      ? [
          {
            key: 'worker',
            warning: `worker|${worker}`,
            icon: AlertTriangle,
            tone: 'warning',
            text: `Mu3Lab’s background worker is ${worker}, so installs, backups and repairs wait until it runs again.`,
            to: '/settings/system',
          },
        ]
      : []),
    // The button that tries again sits in System settings.
    ...(setupFailed
      ? [
          {
            key: 'setup-failed',
            warning: `setup-failed|${data.core.current_job?.id || ''}`,
            icon: AlertTriangle,
            tone: 'warning',
            text: 'A setup step didn’t finish. Open System settings to try again.',
            to: '/settings/system',
          },
        ]
      : []),
    ...failing.map((service) => ({
      key: service.id,
      // An app that recovered in between has a newer last success, so a new failure shows again.
      warning: `app|${service.id}|${service.display_state}|${service.reason}|${service.checks?.process?.last_success_at || ''}`,
      icon: AlertTriangle,
      tone: 'warning',
      text: `${service.name}: ${stateLabel[service.display_state].toLowerCase()}`,
      to: `/apps/${service.id}`,
    })),
    // Sent to administrators only, and only while backups fall behind the schedule.
    ...(setupIncomplete ? [] : data.backup_attention || []).map((item) => ({
      key: item.key,
      warning: `backup|${item.key}|${item.message}`,
      icon: DatabaseBackup,
      tone: 'warning',
      text: item.message,
      to: '/settings/security',
    })),
  ].filter((item) => !dismissed.includes(item.warning));
  // Progress clears by itself, so it cannot be dismissed.
  const progress = working.map((service) => ({
    key: `working-${service.id}`,
    warning: '',
    icon: Loader2,
    tone: 'progress',
    text: `${service.name} is ${stateLabel[service.display_state].toLowerCase()}…`,
    to: `/apps/${service.id}`,
  }));
  const items = [...warnings, ...progress];
  if (!items.length) return null;
  return (
    <section className="attention" aria-label="Needs attention">
      {items.map(({ key, warning, icon: Icon, tone, text, to }) => (
        <div key={key} className="attention-row">
          <Link to={to} className={`attention-item attention-${tone}`}>
            <Icon className={tone === 'progress' ? 'spin' : ''} />
            <span>{text}</span>
            <ArrowRight className="attention-arrow" />
          </Link>
          {warning && (
            <button
              type="button"
              className="btn btn-ghost btn-sm btn-icon"
              aria-label="Hide this warning until it changes"
              title="Hide until it changes"
              onClick={() => dismiss(warning)}
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
  const sections = appSections(services.filter(isInstalled));
  const [folded, toggleFolded] = useFolded();
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
      <CoreSetupProgress />
      <GetStarted />
      <section aria-labelledby="apps-heading">
        <div className="section-title">
          <h2 id="apps-heading">Your apps</h2>
          <Link to="/apps">Manage</Link>
        </div>
        {sections.map(({ id, label, apps }) => {
          const open = !folded.includes(id);
          return (
            <section key={id} className={`launcher-group${open ? '' : ' is-folded'}`}>
              <h3>
                <button type="button" className="launcher-toggle" aria-expanded={open} onClick={() => toggleFolded(id)}>
                  <ChevronDown />
                  {label}
                  <span className="launcher-count">{apps.length}</span>
                </button>
              </h3>
              <div className={open ? 'launcher' : 'launcher-mini'}>
                {apps.map((service) =>
                  open ? (
                    <LauncherTile key={service.id} service={service} onOpen={() => setPanel(service.id)} />
                  ) : (
                    <MiniTile key={service.id} service={service} onOpen={() => setPanel(service.id)} />
                  ),
                )}
              </div>
            </section>
          );
        })}
      </section>
      {selected && <AppQuickPanel service={selected} onClose={() => setPanel('')} />}
    </div>
  );
}
