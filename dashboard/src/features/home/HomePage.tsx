import { ArrowRight, Check, ChevronDown, Play, RefreshCw, Square, X } from 'lucide-react';
import { type CSSProperties, useEffect, useState } from 'react';
import type { Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button } from '../../components/Button';
import { Dot } from '../../components/Status';
import { Link } from '../../lib/router';
import {
  appSections,
  isEverydayApp,
  isInstalled,
  isRunning,
  isWorking,
  launchTarget,
  stateTone,
  statusText,
} from '../../lib/services';
import { useDashboard, useIsAdmin } from '../../state/dashboard';
import { can, type Lifecycle, useLifecycle } from '../apps/lifecycle';
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

const BULK: Lifecycle[] = ['start', 'stop', 'restart'];
const BULK_LOOK = {
  start: { label: 'Start', icon: Play },
  stop: { label: 'Stop', icon: Square },
  restart: { label: 'Restart', icon: RefreshCw },
} as const;

/** An app select mode can pick: one it can start, stop or restart now. */
const pickable = (service: Service) =>
  !isWorking(service) && !service.backing_up && BULK.some((action) => can(service, action));

interface TileProps {
  service: Service;
  onOpen: () => void;
  /** In select mode a tap picks the app instead of opening its panel. */
  selecting: boolean;
  selected: boolean;
}

function tileClass(base: string, { service, selecting, selected }: TileProps) {
  const muted = selecting ? !pickable(service) : !launchTarget(service);
  return `${base}${muted ? ' tile-muted' : ''}${selecting ? ' is-selecting' : ''}${selected ? ' is-selected' : ''}`;
}

/** A folded tile is the icon alone; its name is the tooltip. */
function MiniTile(props: TileProps) {
  const { service, onOpen, selecting, selected } = props;
  return (
    <button
      type="button"
      className={tileClass('mini-tile', props)}
      aria-label={`${service.name}: ${statusText(service)}`}
      title={service.name}
      aria-haspopup={selecting ? undefined : 'dialog'}
      aria-pressed={selecting ? selected : undefined}
      disabled={selecting && !pickable(service)}
      onClick={onOpen}
    >
      <AppIcon id={service.id} />
      {(selecting || !isRunning(service)) && <Dot tone={stateTone(service.display_state)} />}
      {selected && <Check className="tile-check" aria-hidden="true" />}
    </button>
  );
}

/** A tile opens the app's quick panel; the panel's Open button opens the app itself. */
function LauncherTile(props: TileProps) {
  const { service, onOpen, selecting, selected } = props;
  const tone = stateTone(service.display_state);
  return (
    <button
      type="button"
      className={tileClass('tile', props)}
      aria-label={`${service.name}: ${statusText(service)}`}
      aria-haspopup={selecting ? undefined : 'dialog'}
      aria-pressed={selecting ? selected : undefined}
      disabled={selecting && !pickable(service)}
      onClick={onOpen}
    >
      <AppIcon id={service.id} size="lg" />
      <span className="tile-name">{service.name}</span>
      {/* Choosing what to start or stop needs every app's state, running ones too. */}
      {(selecting || !isRunning(service)) && (
        <span className="tile-state">
          <Dot tone={tone} />
          {statusText(service)}
        </span>
      )}
      {selected && <Check className="tile-check" aria-hidden="true" />}
    </button>
  );
}

/** Home's select mode: pick apps, then start, stop or restart them together. */
function useSelection(services: Service[]) {
  const [selecting, setSelecting] = useState(false);
  const [stored, setStored] = useState<string[]>([]);
  // An app that started working on something meanwhile drops out.
  const picked = services.filter((service) => stored.includes(service.id) && pickable(service));
  const toggle = (id: string) =>
    setStored((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]));
  const setMany = (ids: string[], on: boolean) =>
    setStored((current) => (on ? [...new Set([...current, ...ids])] : current.filter((id) => !ids.includes(id))));
  const finish = () => {
    setSelecting(false);
    setStored([]);
  };
  useEffect(() => {
    if (!selecting) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && finish();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selecting]);
  return { selecting, start: () => setSelecting(true), finish, picked, toggle, setMany };
}

function SelectionBar({ picked, onDone }: { picked: Service[]; onDone: () => void }) {
  const lifecycle = useLifecycle();
  const [pending, setPending] = useState<Lifecycle | ''>('');
  const act = async (action: Lifecycle) => {
    setPending(action);
    try {
      if (
        await lifecycle(
          action,
          picked.filter((service) => can(service, action)),
        )
      )
        onDone();
    } finally {
      setPending('');
    }
  };
  return (
    <div className="action-bar selection-bar" role="region" aria-label="Selected apps">
      <span>
        {picked.length ? (
          <>
            <b>{picked.length}</b> selected · {picked.map((service) => service.name).join(', ')}
          </>
        ) : (
          'Tap apps to select them'
        )}
      </span>
      {BULK.map((action) => {
        const count = picked.filter((service) => can(service, action)).length;
        const { label, icon } = BULK_LOOK[action];
        return (
          <Button
            key={action}
            icon={icon}
            variant={action === 'start' ? 'primary' : 'secondary'}
            disabled={!count || Boolean(pending)}
            loading={pending === action}
            title={count && count < picked.length ? `${count} of the selected apps can ${action} now` : undefined}
            onClick={() => void act(action)}
          >
            {label}
            {count > 0 && count < picked.length ? ` ${count}` : ''}
          </Button>
        );
      })}
      <Button variant="ghost" onClick={onDone}>
        Done
      </Button>
    </div>
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
  const isAdmin = useIsAdmin();
  const selection = useSelection(installed);
  const open = (id: string) => (selection.selecting ? selection.toggle(id) : setPanel(id));
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
          <span className="section-title-actions">
            {isAdmin && installed.some(pickable) && (
              <button
                type="button"
                className="link-button"
                aria-pressed={selection.selecting}
                onClick={selection.selecting ? selection.finish : selection.start}
              >
                {selection.selecting ? 'Done' : 'Select'}
              </button>
            )}
            <Link to="/apps">Manage</Link>
          </span>
        </div>
        <div className={`launcher-groups${selection.selecting ? ' is-selecting' : ''}`}>
          {sections.map(({ id, label, apps }) => {
            const unfolded = !isFolded(id);
            const choices = apps.filter(pickable).map((service) => service.id);
            const allPicked = choices.length > 0 && choices.every((app) => selection.picked.some((s) => s.id === app));
            // A group wider than about half the page keeps a row of its own.
            const wide = id === BEHIND_THE_SCENES || apps.length > 4;
            return (
              <section
                key={id}
                className={`launcher-group${unfolded ? '' : ' is-folded'}${wide ? ' is-wide' : ''}`}
                style={{ '--tiles': apps.length } as CSSProperties}
              >
                <h3>
                  <button
                    type="button"
                    className="launcher-toggle"
                    aria-expanded={unfolded}
                    onClick={() => toggleFolded(id)}
                  >
                    <ChevronDown />
                    {label}
                    <span className="launcher-count">{apps.length}</span>
                  </button>
                  {selection.selecting && choices.length > 0 && (
                    <button
                      type="button"
                      className="link-button launcher-pick"
                      onClick={() => selection.setMany(choices, !allPicked)}
                    >
                      {allPicked ? 'Deselect all' : 'Select all'}
                    </button>
                  )}
                </h3>
                <div className={unfolded ? 'launcher' : 'launcher-mini'}>
                  {apps.map((service) => {
                    const props = {
                      service,
                      onOpen: () => open(service.id),
                      selecting: selection.selecting,
                      selected: selection.picked.some((item) => item.id === service.id),
                    };
                    return unfolded ? (
                      <LauncherTile key={service.id} {...props} />
                    ) : (
                      <MiniTile key={service.id} {...props} />
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      </section>
      {selection.selecting && <SelectionBar picked={selection.picked} onDone={selection.finish} />}
      {selected && <AppQuickPanel service={selected} onClose={() => setPanel('')} />}
    </div>
  );
}
