import { ArrowRight, ChevronDown, X } from 'lucide-react';
import { type CSSProperties, useState } from 'react';
import type { Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Dot } from '../../components/Status';
import { Link } from '../../lib/router';
import {
  appSections,
  isEverydayApp,
  isInstalled,
  isRunning,
  launchTarget,
  stateTone,
  statusText,
} from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { AppQuickPanel } from './AppQuickPanel';
import { CoreSetupProgress } from './CoreSetupProgress';
import { GetStarted } from './GetStarted';
import { SystemStatus } from './SystemStatus';
import { useHomeNotices } from './warnings';

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

const FOLDED_KEY = 'mu3lab.homeFolded';
/** The AI and system apps most people never open: one group, last, folded until opened. */
const BEHIND_THE_SCENES = 'behind-the-scenes';
// Stored for that group when it is open, since it starts folded.
const OPENED = `${BEHIND_THE_SCENES}:open`;

/** Sections folded to a row of icons, remembered in this browser. */
function useFolded() {
  const [stored, setStored] = useState<string[]>(() => {
    try {
      const value: unknown = JSON.parse(localStorage.getItem(FOLDED_KEY) || '[]');
      return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
    } catch {
      return [];
    }
  });
  const isFolded = (id: string) => (id === BEHIND_THE_SCENES ? !stored.includes(OPENED) : stored.includes(id));
  const toggle = (id: string) => {
    const item = id === BEHIND_THE_SCENES ? OPENED : id;
    const next = stored.includes(item) ? stored.filter((value) => value !== item) : [...stored, item];
    setStored(next);
    try {
      localStorage.setItem(FOLDED_KEY, JSON.stringify(next));
    } catch {
      // Storage can be unavailable (private mode); the choice just won't persist.
    }
  };
  return [isFolded, toggle] as const;
}

/** A folded tile is the icon alone; its name is the tooltip. */
function MiniTile({ service, onOpen }: { service: Service; onOpen: () => void }) {
  return (
    <button
      type="button"
      className={`mini-tile${launchTarget(service) ? '' : ' tile-muted'}`}
      aria-label={`${service.name}: ${statusText(service)}`}
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
      aria-label={`${service.name}: ${statusText(service)}`}
      aria-haspopup="dialog"
      onClick={onOpen}
    >
      <AppIcon id={service.id} size="lg" />
      <span className="tile-name">{service.name}</span>
      {!isRunning(service) && (
        <span className="tile-state">
          <Dot tone={tone} />
          {statusText(service)}
        </span>
      )}
    </button>
  );
}

function Attention() {
  const { notices, progress, dismiss } = useHomeNotices();
  const items = [...notices, ...progress];
  if (!items.length) return null;
  return (
    <section className="attention" aria-label="Needs attention">
      {items.map(({ key, warnings, icon: Icon, tone, text, more, to }) => (
        <div key={key} className="attention-row">
          <Link to={to} className={`attention-item attention-${tone}`}>
            <Icon className={tone === 'progress' ? 'spin' : ''} />
            <span>
              {text}
              {more > 0 && <small className="attention-more"> +{more} more</small>}
            </span>
            <ArrowRight className="attention-arrow" />
          </Link>
          {warnings.length > 0 && (
            <button
              type="button"
              className="btn btn-ghost btn-sm btn-icon"
              aria-label="Hide this warning until it changes"
              title="Hide until it changes"
              onClick={() => dismiss(warnings)}
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
  const installed = services.filter(isInstalled);
  const plumbing = installed.filter((service) => !isEverydayApp(service));
  const sections = [
    ...appSections(installed.filter(isEverydayApp)),
    ...(plumbing.length ? [{ id: BEHIND_THE_SCENES, label: 'AI & system', apps: plumbing }] : []),
  ];
  const [isFolded, toggleFolded] = useFolded();
  const name = (data.identity.display_name || data.identity.username || '').split(' ')[0];
  const [panel, setPanel] = useState('');
  // Look the app up each render so the panel follows its live state.
  const selected = services.find((service) => service.id === panel);
  return (
    <div className="home">
      <header className="home-header">
        <div>
          <h1>
            {greeting()}
            {name ? `, ${name}` : ''}
          </h1>
          <p>{new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</p>
        </div>
        <SystemStatus />
      </header>
      <Attention />
      <CoreSetupProgress />
      <GetStarted />
      <section aria-labelledby="apps-heading">
        <div className="section-title">
          <h2 id="apps-heading">Your apps</h2>
          <Link to="/apps">Manage</Link>
        </div>
        <div className="launcher-groups">
          {sections.map(({ id, label, apps }) => {
            const open = !isFolded(id);
            // A group wider than about half the page keeps a row of its own.
            const wide = id === BEHIND_THE_SCENES || apps.length > 4;
            return (
              <section
                key={id}
                className={`launcher-group${open ? '' : ' is-folded'}${wide ? ' is-wide' : ''}`}
                style={{ '--tiles': apps.length } as CSSProperties}
              >
                <h3>
                  <button
                    type="button"
                    className="launcher-toggle"
                    aria-expanded={open}
                    onClick={() => toggleFolded(id)}
                  >
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
        </div>
      </section>
      {selected && <AppQuickPanel service={selected} onClose={() => setPanel('')} />}
    </div>
  );
}
