import { AlertTriangle, CircleStop, ExternalLink, KeyRound, Loader2, Play, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import {
  api,
  type HomeWidgetData,
  type HomeWidgetItem,
  type HomeWidgetKeyStatus,
  type HomeWidgetKind,
  postJsonApi,
  putJsonApi,
} from '../../../api';
import { Button, LinkButton } from '../../../components/Button';
import { errorText } from '../../../lib/format';
import { useApi } from '../../../lib/useApi';
import { launchTarget } from '../../../lib/services';
import { useDashboard, useIsAdmin } from '../../../state/dashboard';
import { useLifecycle } from '../../apps/lifecycle';
import { FitList, useBox } from './size';
import type { SettingsProps, WidgetProps } from './widgets';

const dataPath = (kind: string) => `/api/v1/home/widgets/${encodeURIComponent(kind)}/data`;

/** "2 min ago", for when the figures were read. */
function since(seconds: number) {
  const age = Math.max(0, Date.now() / 1000 - seconds);
  if (age < 60) return 'just now';
  if (age < 3600) return `${Math.floor(age / 60)} min ago`;
  return `${Math.floor(age / 3600)} h ago`;
}

/** Ask the app again now, skipping the figures Mu3Lab keeps between refreshes. */
export async function refreshWidget(kind: string) {
  await api<HomeWidgetData>(`${dataPath(kind)}?fresh=1`);
}

/** Placeholders shaped like the figures, while the first ones load. */
function Skeleton() {
  return (
    <div className="appw-skeleton" aria-hidden>
      <div className="appw-stats">
        {[0, 1, 2].map((index) => (
          <span key={index} className="appw-stat">
            <b />
            <i />
          </span>
        ))}
      </div>
      {[0, 1, 2].map((index) => (
        <span key={index} className="appw-skeleton-row" />
      ))}
    </div>
  );
}

/** Why there are no figures, and the one thing that fixes it. */
function Unavailable({
  data,
  onRetry,
  onConfigure,
  takesKey,
  tiny,
}: {
  data: HomeWidgetData;
  onRetry: () => void;
  onConfigure: () => void;
  takesKey: boolean;
  /** A short widget: the sentence on one or two lines, with its button beside it. */
  tiny: boolean;
}) {
  const isAdmin = useIsAdmin();
  const lifecycle = useLifecycle();
  const service = useDashboard().data.services.services.find((item) => item.id === data.app_id);
  const open = service ? launchTarget(service) : null;
  return (
    <div className={`appw-state appw-${data.state}${tiny ? ' is-tiny' : ''}`}>
      <p title={data.detail}>{data.detail}</p>
      {data.state === 'setup' && takesKey && (
        <Button variant="secondary" size="sm" icon={KeyRound} onClick={onConfigure}>
          Add the key
        </Button>
      )}
      {data.state === 'setup' && !takesKey && open && (
        <a className="btn btn-secondary btn-sm" href={open.url} target="_blank" rel="noreferrer">
          Open {data.app_name}
        </a>
      )}
      {data.state === 'stopped' && isAdmin && service && (
        <Button variant="secondary" size="sm" icon={Play} onClick={() => void lifecycle('start', [service])}>
          Start {data.app_name}
        </Button>
      )}
      {data.state === 'stopped' && !isAdmin && <small>An administrator can start it.</small>}
      {data.state === 'not_installed' && isAdmin && (
        <LinkButton to={`/apps/${data.app_id}`} size="sm">
          See {data.app_name}
        </LinkButton>
      )}
      {data.state === 'not_installed' && !isAdmin && <small>Remove this widget, or ask an administrator.</small>}
      {data.state === 'error' && (
        <Button variant="secondary" size="sm" icon={RefreshCw} onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

/**
 * Above the last known figures while the app cannot give new ones: why, how old they are, and the
 * fix. Grey for a stopped or starting app, amber when it is running but did not answer.
 */
function SavedBanner({ data, onRetry, tiny }: { data: HomeWidgetData; onRetry: () => void; tiny: boolean }) {
  const isAdmin = useIsAdmin();
  const lifecycle = useLifecycle();
  const service = useDashboard().data.services.services.find((item) => item.id === data.app_id);
  // A short widget: a few words, so the figures still fit beneath.
  const why =
    data.state === 'starting'
      ? tiny
        ? 'Starting'
        : `${data.app_name} is starting`
      : data.state === 'error'
        ? tiny
          ? 'No answer'
          : `Couldn’t reach ${data.app_name}`
        : tiny
          ? 'Stopped'
          : `${data.app_name} is stopped`;
  const Icon = data.state === 'starting' ? Loader2 : data.state === 'error' ? AlertTriangle : CircleStop;
  return (
    <p className={`appw-saved is-${data.state}${tiny ? ' is-tiny' : ''}`} role="status" title={data.detail}>
      <Icon aria-hidden />
      <span>
        {why} · {tiny ? '' : 'figures from '}
        {since(data.updated_at ?? 0)}
      </span>
      {data.state === 'stopped' && isAdmin && service && (
        <button type="button" onClick={() => void lifecycle('start', [service])}>
          Start
        </button>
      )}
      {data.state === 'error' && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </p>
  );
}

function Item({ item, onAct }: { item: HomeWidgetItem; onAct?: (action: string, done: boolean) => Promise<void> }) {
  // Ticking shows at once; it goes back if the app does not take it.
  const [pending, setPending] = useState<boolean | null>(null);
  const checked = pending ?? item.check ?? false;
  if (item.check !== null && item.check !== undefined && item.action)
    return (
      <li>
        <label className={`appw-item appw-check${checked ? ' is-done' : ''}`}>
          <input
            type="checkbox"
            checked={checked}
            // Last known figures: the app cannot take a tick until it answers again.
            disabled={pending !== null || !onAct}
            onChange={(event) => {
              if (!onAct) return;
              const next = event.target.checked;
              setPending(next);
              void onAct(item.action ?? '', next).finally(() => setPending(null));
            }}
          />
          <span className="appw-item-text">
            <strong>{item.title}</strong>
            {item.subtitle && <small>{item.subtitle}</small>}
          </span>
          {item.meta && <span className={`appw-meta${item.tone ? ` tone-${item.tone}` : ''}`}>{item.meta}</span>}
        </label>
      </li>
    );
  const body = (
    <>
      {item.tone && <span className={`appw-dot tone-${item.tone}`} aria-hidden />}
      <span className="appw-item-text">
        <strong>{item.title}</strong>
        {item.subtitle && <small>{item.subtitle}</small>}
        {item.progress !== null && item.progress !== undefined && (
          <span className="appw-progress" role="progressbar" aria-valuenow={Math.round(item.progress * 100)}>
            <span style={{ width: `${item.progress * 100}%` }} />
          </span>
        )}
      </span>
      {item.meta && <span className={`appw-meta${item.tone ? ` tone-${item.tone}` : ''}`}>{item.meta}</span>}
      {item.url && <ExternalLink className="appw-open" aria-hidden />}
    </>
  );
  return (
    <li>
      {item.url ? (
        <a className="appw-item" href={item.url} target="_blank" rel="noreferrer">
          {body}
        </a>
      ) : (
        <div className="appw-item">{body}</div>
      )}
    </li>
  );
}

/**
 * Every app's widget, drawn the same way: headline figures on top, then a list, and a plain
 * sentence with the fix whenever the app cannot answer. Figures refresh on the widget's own clock,
 * pause while the tab is hidden, and stay on screen (marked) when a refresh fails.
 */
export function AppWidget({ widget, kind, onConfigure }: WidgetProps & { kind?: HomeWidgetKind }) {
  const { width, height } = useBox();
  const measured = width > 0;
  const tiny = measured && height < 110;
  const service = useDashboard().data.services.services.find((item) => item.id === kind?.app_id);
  const open = service ? launchTarget(service) : null;
  const seconds = kind?.refresh_seconds ?? 300;
  const { data, error, reload } = useApi<HomeWidgetData>(dataPath(widget.kind), { interval: seconds * 1000 });
  const retry = () =>
    void refreshWidget(widget.kind)
      .catch(() => undefined)
      .then(() => reload());
  const act = async (action: string, done: boolean) => {
    try {
      await postJsonApi(`/api/v1/home/widgets/${encodeURIComponent(widget.kind)}/action`, { action });
      await reload();
    } catch (cause) {
      toast.error(`${done ? 'Not ticked' : 'Not unticked'}: ${errorText(cause)}`);
    }
  };
  if (!data) return error ? <p className="appw-state appw-error">{error}</p> : <Skeleton />;
  // The app cannot answer now; when Mu3Lab kept its last figures, they show (greyed, with their age).
  const saved = data.state !== 'ok' && Boolean(data.saved);
  if (data.state !== 'ok' && !saved)
    return (
      <Unavailable
        data={data}
        onRetry={retry}
        onConfigure={onConfigure}
        takesKey={Boolean(kind?.key_label)}
        tiny={tiny}
      />
    );
  const stats = data.stats ?? [];
  const items = data.items ?? [];
  const updated = data.updated_at ?? 0;
  const hasList = items.length > 0 || Boolean(data.empty);
  // Short: the figures alone (or the list alone, if there are no figures). Wide: figures beside the
  // list. Otherwise figures above the list. Whatever does not fit whole is left out, never cut.
  const side = measured && width >= 560 && stats.length > 0 && hasList;
  const showList = hasList && (!tiny || !stats.length || side);
  // Not much height: each row on one line (its title and figure), and no list heading.
  const compact = measured && height < 240;
  const statTiles = stats.map((stat) => (
    <li
      key={stat.label}
      className={`appw-stat${stat.tone ? ` tone-${stat.tone}` : ''}`}
      title={`${stat.label}: ${stat.value}`}
    >
      <b>{stat.value}</b>
      <i>{stat.label}</i>
    </li>
  ));
  const more = (hidden: number) =>
    open ? (
      <a href={open.url} target="_blank" rel="noreferrer">
        +{hidden} more in {data.app_name}
      </a>
    ) : (
      `+${hidden} more`
    );
  return (
    <div
      className={`appw${side ? ' is-side' : ''}${tiny ? ' is-tiny' : ''}${compact ? ' is-compact' : ''}${showList ? '' : ' is-figures'}${saved ? ' is-saved' : ''}`}
      title={updated ? `Updated ${since(updated)}` : undefined}
    >
      {saved && <SavedBanner data={data} onRetry={retry} tiny={tiny} />}
      {error && !saved && !tiny && (
        <p className="appw-stale" role="status">
          <AlertTriangle aria-hidden /> Couldn’t refresh: showing figures from {since(updated)}.{' '}
          <span className="sr-only">{errorText(error)}</span>
        </p>
      )}
      {data.note && !tiny && <p className="appw-note">{data.note}</p>}
      <div className="appw-main">
        {stats.length > 0 &&
          (showList && !side ? (
            // Above a list: one row of figures.
            <ul className="appw-stats is-row">{statTiles}</ul>
          ) : (
            <FitList className="appw-stats" label="Figures">
              {statTiles}
            </FitList>
          ))}
        {showList && (
          <div className="appw-list">
            {data.items_title && items.length > 0 && !compact && <h4>{data.items_title}</h4>}
            {items.length ? (
              <FitList more={more}>
                {items.map((item, index) => (
                  <Item key={`${item.title}-${index}`} item={item} onAct={saved ? undefined : act} />
                ))}
              </FitList>
            ) : (
              <p className="appw-empty">{data.empty}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Settings for an app widget that needs a key Mu3Lab cannot make: pasted once, never shown again. */
export function KeySettings({ widget, kind }: SettingsProps & { kind?: HomeWidgetKind }) {
  const path = `/api/v1/home/widgets/${encodeURIComponent(widget.kind)}/key`;
  const { data, reload } = useApi<HomeWidgetKeyStatus>(path);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async (key: string) => {
    setBusy(true);
    try {
      await putJsonApi<HomeWidgetKeyStatus>(path, { key });
      setValue('');
      await reload();
      await refreshWidget(widget.kind).catch(() => undefined);
      toast.success(key ? 'Key saved. The widget shows your figures within a moment.' : 'Key removed.');
    } catch (cause) {
      toast.error(`The key was not saved: ${errorText(cause)}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="board-key">
      <label className="field">
        <span>{kind?.key_label ?? 'Key'}</span>
        <input
          type="password"
          autoComplete="off"
          value={value}
          placeholder={data?.saved ? 'Saved (paste a new one to replace it)' : 'Paste it here'}
          onChange={(event) => setValue(event.target.value)}
        />
      </label>
      {kind?.key_help && <p className="muted board-key-help">{kind.key_help}</p>}
      <div className="board-key-actions">
        <Button variant="primary" size="sm" disabled={!value.trim()} loading={busy} onClick={() => void save(value)}>
          Save key
        </Button>
        {data?.saved && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => void save('')}>
            Remove key
          </Button>
        )}
      </div>
    </div>
  );
}
