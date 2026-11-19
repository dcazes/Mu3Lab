import { Check, ChevronDown, MessageCircle, MessageCircleOff, Play, RefreshCw, Square } from 'lucide-react';
import { type CSSProperties, type ReactNode, useEffect, useState } from 'react';
import type { ChatAssistant, Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button } from '../../components/Button';
import { Dot } from '../../components/Status';
import {
  isRunning,
  isWorking,
  type LauncherSection,
  launchTarget,
  launcherSections,
  sectionSummary,
  stateTone,
  statusText,
} from '../../lib/services';
import { useDashboard, useIsAdmin } from '../../state/dashboard';
import { AppDetailsCard, AppsSummaryCard } from './AppDetailsCard';
import { can, type Lifecycle, useLifecycle } from './lifecycle';

const FOLDED_KEY = 'mu3lab.homeFolded';
// Stored for a section that starts folded (AI, System) once someone opens it.
const opened = (id: string) => `${id}:open`;

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
  const isFolded = (section: LauncherSection) =>
    section.foldedByDefault ? !stored.includes(opened(section.id)) : stored.includes(section.id);
  const toggle = (section: LauncherSection) => {
    const item = section.foldedByDefault ? opened(section.id) : section.id;
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
export const pickable = (service: Service) =>
  !isWorking(service) && !service.backing_up && BULK.some((action) => can(service, action));

/** This person's chat assistant for an app, if it has one: ready, or there but unavailable. */
function ChatMark({ assistant }: { assistant?: ChatAssistant }) {
  if (!assistant) return null;
  const ready = assistant.status === 'ready';
  const Icon = ready ? MessageCircle : MessageCircleOff;
  const label = ready ? 'Chat assistant works' : 'Chat assistant unavailable';
  return (
    <span className={`tile-chat${ready ? ' is-ready' : ''}`} title={`${label}. ${assistant.detail}`}>
      <Icon aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

interface TileProps {
  service: Service;
  assistant?: ChatAssistant;
  onOpen: () => void;
  /** In select mode a tap picks the app instead of opening its panel. */
  selecting: boolean;
  selected: boolean;
  /** Its details are showing in the card. */
  current?: boolean;
}

function tileClass(base: string, { service, selecting, selected, current }: TileProps) {
  const muted = selecting ? !pickable(service) : !launchTarget(service);
  const off = !isRunning(service) && !isWorking(service) && !service.backing_up;
  return `${base}${muted ? ' tile-muted' : ''}${off ? ' is-off' : ''}${selecting ? ' is-selecting' : ''}${selected ? ' is-selected' : ''}${current ? ' is-current' : ''}`;
}

/** Running needs no mark; anything else shows as a dot on the icon's corner, so every tile is the same size. */
function StateDot({ service }: { service: Service }) {
  if (isRunning(service) && !service.backing_up) return null;
  return <Dot tone={service.backing_up ? 'blue' : stateTone(service.display_state)} />;
}

/** A folded tile is the icon alone; its name is the tooltip. */
function MiniTile(props: TileProps) {
  const { service, onOpen, selecting, selected } = props;
  return (
    <button
      type="button"
      className={tileClass('mini-tile', props)}
      aria-label={`${service.name}: ${statusText(service)}`}
      title={`${service.name}: ${statusText(service)}`}
      aria-pressed={selecting ? selected : props.current}
      disabled={selecting && !pickable(service)}
      onClick={onOpen}
    >
      <span className="tile-icon">
        <AppIcon id={service.id} />
        <StateDot service={service} />
      </span>
      {selected && <Check className="tile-check" aria-hidden="true" />}
    </button>
  );
}

/** A tile opens the app's quick panel; the panel's Open button opens the app itself. */
function LauncherTile(props: TileProps) {
  const { service, assistant, onOpen, selecting, selected } = props;
  return (
    <button
      type="button"
      className={tileClass('tile', props)}
      aria-label={`${service.name}: ${statusText(service)}`}
      title={isRunning(service) ? undefined : statusText(service)}
      aria-pressed={selecting ? selected : props.current}
      disabled={selecting && !pickable(service)}
      onClick={onOpen}
    >
      <span className="tile-icon">
        <AppIcon id={service.id} size="lg" />
        <StateDot service={service} />
      </span>
      <span className="tile-name">{service.name}</span>
      {selected ? <Check className="tile-check" aria-hidden="true" /> : <ChatMark assistant={assistant} />}
    </button>
  );
}

/** Select mode: pick apps, then start, stop or restart them together. */
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

/**
 * Start the stopped apps of a section, or stop a whole stack. Goes through the same
 * dependency prompts as any other start or stop (``useLifecycle``).
 */
function SectionAction({ section }: { section: LauncherSection }) {
  const lifecycle = useLifecycle();
  const [pending, setPending] = useState(false);
  const idle = section.apps.filter((service) => !isWorking(service) && !service.backing_up);
  const startable = idle.filter((service) => !isRunning(service) && can(service, 'start'));
  const stoppable = idle.filter((service) => isRunning(service) && can(service, 'stop'));
  const action: Lifecycle | '' = startable.length ? 'start' : section.stack && stoppable.length ? 'stop' : '';
  if (!action) return null;
  const apps = action === 'start' ? startable : stoppable;
  const { icon } = BULK_LOOK[action];
  return (
    <Button
      size="sm"
      variant="ghost"
      icon={icon}
      loading={pending}
      className="launcher-action"
      onClick={async () => {
        setPending(true);
        try {
          await lifecycle(action, apps);
        } finally {
          setPending(false);
        }
      }}
    >
      {action === 'start' ? (section.apps.length === 1 ? 'Start' : 'Start all') : 'Stop all'}
    </Button>
  );
}

/**
 * Installed apps in sections: everyday apps by category, the stacks that only work together
 * (with how many of each are running), then Automation, AI and Backups & system. Picking a tile
 * shows its details in a card beside the tiles (at the bottom on a phone), never a pop-up.
 *
 * ``foldedIcons``: a folded section still shows its apps as small icons (Apps); without it only
 * the heading shows (Home). ``listRow``: show each section's apps as rows instead of tiles.
 */
export function AppStacks({
  services,
  title,
  foldedIcons = true,
  listRow,
}: {
  services: Service[];
  title?: string;
  foldedIcons?: boolean;
  listRow?: (service: Service) => ReactNode;
}) {
  const { data } = useDashboard();
  const all = data.services.services;
  const assistants = new Map((data.chat.assistants || []).map((item) => [item.id, item]));
  const sections = launcherSections(services);
  const [isFolded, toggleFolded] = useFolded();
  const [panel, setPanel] = useState('');
  const isAdmin = useIsAdmin();
  const selection = useSelection(services);
  const open = (id: string) =>
    selection.selecting ? selection.toggle(id) : setPanel((current) => (current === id ? '' : id));
  // Look the app up each render so the card follows its live state.
  const selected = all.find((service) => service.id === panel);
  useEffect(() => {
    if (!panel) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setPanel('');
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [panel]);
  const showCard = !listRow && !selection.selecting;
  return (
    <>
      <div className="section-title">
        {title ? <h2 id="apps-heading">{title}</h2> : <span />}
        {isAdmin && services.some(pickable) && (
          <span className="section-title-actions">
            <button
              type="button"
              className="link-button"
              aria-pressed={selection.selecting}
              onClick={() => {
                setPanel('');
                if (selection.selecting) selection.finish();
                else selection.start();
              }}
            >
              {selection.selecting ? 'Done' : 'Select'}
            </button>
          </span>
        )}
      </div>
      <div className={`stacks-layout${showCard ? ' has-card' : ''}${selected && showCard ? ' has-pick' : ''}`}>
        <div className={`launcher-groups${selection.selecting ? ' is-selecting' : ''}${listRow ? ' is-list' : ''}`}>
          {sections.map((section) => {
            const { id, label, apps, stack } = section;
            const unfolded = !isFolded(section);
            const choices = apps.filter(pickable).map((service) => service.id);
            const allPicked = choices.length > 0 && choices.every((app) => selection.picked.some((s) => s.id === app));
            const summary = sectionSummary(section);
            // Lists, stacks and backstage sections always get a row of their own; a small category can share one.
            const wide = Boolean(listRow) || stack || section.foldedByDefault || apps.length > 4;
            return (
              <section
                key={id}
                className={`launcher-group${unfolded ? '' : ' is-folded'}${wide ? ' is-wide' : ''}${stack ? ' is-stack' : ''}`}
                style={{ '--tiles': apps.length } as CSSProperties}
                aria-label={label}
              >
                <h3>
                  <button
                    type="button"
                    className="launcher-toggle"
                    aria-expanded={unfolded}
                    onClick={() => toggleFolded(section)}
                  >
                    <ChevronDown />
                    {label}
                  </button>
                  <span className={`launcher-summary tone-${summary.tone}`}>
                    <span className="launcher-count">{summary.count}</span>
                    {summary.detail && <span className="launcher-detail"> · {summary.detail}</span>}
                  </span>
                  {selection.selecting
                    ? choices.length > 0 && (
                        <button
                          type="button"
                          className="link-button launcher-pick"
                          onClick={() => selection.setMany(choices, !allPicked)}
                        >
                          {allPicked ? 'Deselect all' : 'Select all'}
                        </button>
                      )
                    : isAdmin && <SectionAction section={section} />}
                </h3>
                {unfolded && listRow ? (
                  <div className="card card-flush rows">{apps.map((service) => listRow(service))}</div>
                ) : unfolded || foldedIcons ? (
                  <div className={unfolded ? 'launcher' : 'launcher-mini'}>
                    {apps.map((service) => {
                      const props = {
                        service,
                        assistant: assistants.get(service.id),
                        onOpen: () => open(service.id),
                        selecting: selection.selecting,
                        selected: selection.picked.some((item) => item.id === service.id),
                        current: showCard && service.id === panel,
                      };
                      return unfolded ? (
                        <LauncherTile key={service.id} {...props} />
                      ) : (
                        <MiniTile key={service.id} {...props} />
                      );
                    })}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
        {showCard &&
          (selected ? (
            <AppDetailsCard key={selected.id} service={selected} onClose={() => setPanel('')} />
          ) : (
            <AppsSummaryCard services={services} onPick={setPanel} />
          ))}
      </div>
      {selection.selecting && <SelectionBar picked={selection.picked} onDone={selection.finish} />}
    </>
  );
}
