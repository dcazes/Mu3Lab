import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { Button } from '../../../components/Button';
import { FitList, useBox } from './size';
import type { SettingsProps, WidgetProps } from './widgets';

interface Link {
  url: string;
  label: string;
}

interface BookmarkSettings {
  links?: Link[];
  layout?: 'tiles' | 'list';
}

const MAX = 40;
const settingsOf = (widget: WidgetProps['widget']) => widget.settings as BookmarkSettings;

/** "github.com" from a link. */
function hostOf(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

/** A link's badge: its first letter on a colour of its own (no icon is fetched from the site). */
function Badge({ link }: { link: Link }) {
  const name = link.label || hostOf(link.url);
  let hash = 0;
  for (const char of hostOf(link.url)) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return (
    <span className="bookmark-badge" style={{ '--badge-hue': hash } as React.CSSProperties} aria-hidden>
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

/** The person's links, as tiles or a list; each opens in a new tab. */
export function BookmarksWidget({ widget, onConfigure }: WidgetProps) {
  const { links = [], layout = 'tiles' } = settingsOf(widget);
  const { width, height } = useBox();
  if (!links.length)
    return (
      <div className="widget-empty">
        <p>No links yet.</p>
        <Button variant="secondary" size="sm" icon={Plus} onClick={onConfigure}>
          Add links
        </Button>
      </div>
    );
  // Short: small chips (badge and name side by side) that wrap, so a two-row widget still shows a few.
  if (layout === 'list' || (height > 0 && height < 100))
    return (
      <FitList
        className={layout === 'list' ? 'bookmark-list' : 'bookmark-chips'}
        label="Links"
        more={(hidden) => `+${hidden} more`}
      >
        {links.map((link) => (
          <li key={link.url}>
            <a href={link.url} target="_blank" rel="noreferrer">
              <Badge link={link} />
              <span className="bookmark-name">{link.label || hostOf(link.url)}</span>
              {layout === 'list' && <small className="muted">{hostOf(link.url)}</small>}
            </a>
          </li>
        ))}
      </FitList>
    );
  // Tiles fill the width in as many columns as fit, then only the rows that fit whole.
  const columns = Math.max(1, Math.floor((width || 400) / 92));
  return (
    <FitList className="bookmark-tiles" label="Links" more={(hidden) => `+${hidden} more`}>
      {links.map((link) => (
        <li key={link.url} style={{ width: `calc(${100 / columns}% - 6px)` }}>
          <a href={link.url} target="_blank" rel="noreferrer" title={link.url}>
            <Badge link={link} />
            <span className="bookmark-name">{link.label || hostOf(link.url)}</span>
          </a>
        </li>
      ))}
    </FitList>
  );
}

export function BookmarksSettings({ widget, onChange }: SettingsProps) {
  const settings = settingsOf(widget);
  const links = settings.links ?? [];
  const [url, setUrl] = useState('');
  const [label, setLabel] = useState('');
  const address = /^https?:\/\//i.test(url.trim()) ? url.trim() : url.trim() ? `https://${url.trim()}` : '';
  const valid = (() => {
    try {
      return Boolean(address) && Boolean(new URL(address).hostname.includes('.')) && links.length < MAX;
    } catch {
      return false;
    }
  })();
  const set = (patch: BookmarkSettings) => onChange({ ...settings, ...patch });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    set({ links: [...links, { url: address, label: label.trim() }] });
    setUrl('');
    setLabel('');
  };
  const move = (index: number, by: number) => {
    const next = [...links];
    const [moved] = next.splice(index, 1);
    next.splice(index + by, 0, moved);
    set({ links: next });
  };
  return (
    <>
      <div className="widget-setting">
        <span className="widget-setting-label">Show as</span>
        <div className="segmented" role="radiogroup" aria-label="Show as">
          {(
            [
              ['tiles', 'Tiles'],
              ['list', 'List'],
            ] as const
          ).map(([value, text]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={(settings.layout ?? 'tiles') === value}
              onClick={() => set({ layout: value })}
            >
              {text}
            </button>
          ))}
        </div>
      </div>
      <div className="widget-setting">
        <span className="widget-setting-label">Links</span>
        {links.length > 0 && (
          <ul className="bookmark-edit">
            {links.map((link, index) => (
              <li key={link.url}>
                <input
                  className="widget-input"
                  aria-label={`Name for ${hostOf(link.url)}`}
                  placeholder={hostOf(link.url)}
                  maxLength={40}
                  value={link.label}
                  onChange={(event) =>
                    set({
                      links: links.map((each, at) => (at === index ? { ...each, label: event.target.value } : each)),
                    })
                  }
                />
                <button
                  type="button"
                  className="widget-tool"
                  aria-label={`Move ${link.label || hostOf(link.url)} up`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUp />
                </button>
                <button
                  type="button"
                  className="widget-tool"
                  aria-label={`Move ${link.label || hostOf(link.url)} down`}
                  disabled={index === links.length - 1}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDown />
                </button>
                <button
                  type="button"
                  className="widget-tool"
                  aria-label={`Remove ${link.label || hostOf(link.url)}`}
                  onClick={() => set({ links: links.filter((_, at) => at !== index) })}
                >
                  <X />
                </button>
              </li>
            ))}
          </ul>
        )}
        {links.length < MAX && (
          <form className="bookmark-add" onSubmit={submit}>
            <input
              className="widget-input"
              aria-label="Address"
              placeholder="Address, like nas.example.com"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
            <input
              className="widget-input"
              aria-label="Name (optional)"
              placeholder="Name (optional)"
              maxLength={40}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
            <Button type="submit" variant="secondary" size="sm" icon={Plus} disabled={!valid}>
              Add link
            </Button>
          </form>
        )}
      </div>
    </>
  );
}
