import { Plus, X } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import type { FeedsResponse } from '../../../api';
import { Button } from '../../../components/Button';
import { useApi } from '../../../lib/useApi';
import { FitList, useBox } from './size';
import type { SettingsProps, WidgetProps } from './widgets';

interface RssSettings {
  feeds?: string[];
  summaries?: boolean;
}

const MAX = 8;
// The server keeps each feed 15 minutes; the board asks again as often while it is open.
const EVERY = 15 * 60_000;

function hostOf(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

/** "3 h", "2 d": how long ago an item came out. */
function age(seconds: number) {
  if (!seconds) return '';
  const minutes = Math.max(0, (Date.now() / 1000 - seconds) / 60);
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))} min`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 60 / 24)} d`;
}

/** The newest items of the person's feeds, together and newest first, each with where it is from. */
export function RssWidget({ widget, onConfigure }: WidgetProps) {
  const { feeds = [], summaries = false } = widget.settings as RssSettings;
  const { height } = useBox();
  const query = feeds.map((url) => `url=${encodeURIComponent(url)}`).join('&');
  const { data, error } = useApi<FeedsResponse>(feeds.length ? `/api/v1/home/feeds?${query}` : null, {
    interval: EVERY,
  });
  if (!feeds.length)
    return (
      <div className="widget-empty">
        <p>No news feeds yet.</p>
        <Button variant="secondary" size="sm" icon={Plus} onClick={onConfigure}>
          Add a feed
        </Button>
      </div>
    );
  if (!data) return error ? <p className="widget-error">{error}</p> : <p className="muted">Loading…</p>;
  const items = data.feeds
    .flatMap((feed) =>
      (feed.items ?? []).map((item) => ({
        ...item,
        source: feed.title || hostOf(feed.url),
        key: `${feed.url}|${item.url}`,
      })),
    )
    .sort((a, b) => (b.published ?? 0) - (a.published ?? 0));
  const failed = data.feeds.filter((feed) => feed.error);
  const withSummary = summaries && height >= 160;
  if (!items.length)
    return <p className="widget-error">{failed[0]?.error || 'These feeds have nothing to show yet.'}</p>;
  return (
    <FitList className={`rss-list${withSummary ? ' has-summaries' : ''}`} label="News">
      {items.map((item) => (
        <li key={item.key}>
          <a href={item.url} target="_blank" rel="noreferrer">
            <span className="rss-title">{item.title}</span>
            {withSummary && item.summary && <span className="rss-summary">{item.summary}</span>}
            <small className="muted">
              {item.source}
              {item.published ? ` · ${age(item.published)}` : ''}
            </small>
          </a>
        </li>
      ))}
    </FitList>
  );
}

export function RssSettings({ widget, onChange }: SettingsProps) {
  const settings = widget.settings as RssSettings;
  const feeds = settings.feeds ?? [];
  const [value, setValue] = useState('');
  const address = /^https?:\/\//i.test(value.trim()) ? value.trim() : value.trim() ? `https://${value.trim()}` : '';
  const valid = Boolean(address) && !feeds.includes(address) && feeds.length < MAX && address.includes('.');
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    onChange({ ...settings, feeds: [...feeds, address] });
    setValue('');
  };
  return (
    <>
      <div className="widget-setting">
        <span className="widget-setting-label">Feeds</span>
        {feeds.length > 0 && (
          <ul className="chip-list is-wide">
            {feeds.map((url) => (
              <li key={url} title={url}>
                {hostOf(url)}
                <button
                  type="button"
                  aria-label={`Remove ${hostOf(url)}`}
                  onClick={() => onChange({ ...settings, feeds: feeds.filter((item) => item !== url) })}
                >
                  <X />
                </button>
              </li>
            ))}
          </ul>
        )}
        {feeds.length < MAX && (
          <form className="inline-form" onSubmit={submit}>
            <input
              className="widget-input"
              aria-label="Feed address"
              placeholder="Feed address (RSS or Atom)"
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
            <Button type="submit" variant="secondary" size="sm" icon={Plus} disabled={!valid}>
              Add
            </Button>
          </form>
        )}
        <p className="muted widget-setting-hint">
          Most news sites and blogs have one, often ending in /feed or /rss. Mu3Lab reads them, so only public addresses
          work.
        </p>
      </div>
      <label className="board-panel-switch">
        <input
          type="checkbox"
          checked={settings.summaries === true}
          onChange={(event) => onChange({ ...settings, summaries: event.target.checked })}
        />
        <span>
          Short summaries
          <small>Under each title, when the widget is tall enough.</small>
        </span>
      </label>
    </>
  );
}
