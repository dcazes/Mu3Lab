import { ChevronLeft, ChevronRight, Pause, Play } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { HomeWidgetData, HomeWidgetKind } from '../../../api';
import { useApi } from '../../../lib/useApi';
import { dataPath, refreshWidget, Unavailable } from './AppWidget';
import { useBox } from './size';
import type { SettingsProps, WidgetProps } from './widgets';

type Source = 'album' | 'favorites' | 'memories' | 'random';

interface SlideshowSettings {
  source?: Source;
  album_id?: string;
  seconds?: number;
  fit?: 'cover' | 'contain';
}

interface Slide {
  id: string;
  taken: string;
  place: string;
}

interface SlideshowView {
  slides?: Slide[];
  albums?: { id: string; name: string }[];
}

const SOURCES: [Source, string][] = [
  ['memories', 'On this day'],
  ['album', 'An album'],
  ['favorites', 'Favourites'],
  ['random', 'Random from my library'],
];
const SPEEDS = [5, 8, 12, 20, 30, 60];

/** The settings the server reads the photos with: only the ones that choose them. */
const queryOf = (settings: SlideshowSettings) =>
  encodeURIComponent(
    JSON.stringify({
      source: settings.source ?? 'memories',
      album_id: settings.album_id ?? '',
      seconds: settings.seconds ?? 12,
    }),
  );

const fileOf = (kind: string, id: string, settings: SlideshowSettings) =>
  `/api/v1/home/widgets/${encodeURIComponent(kind)}/file/${id}?settings=${queryOf(settings)}`;

function takenOn(text: string) {
  const when = Date.parse(text);
  return Number.isFinite(when)
    ? new Date(when).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
    : '';
}

/** The person's own photos, one after another, each read from Immich through Mu3Lab as them. */
export function SlideshowWidget({ widget, kind, onConfigure }: WidgetProps & { kind?: HomeWidgetKind }) {
  const settings = widget.settings as SlideshowSettings;
  const { height } = useBox();
  const tiny = height > 0 && height < 110;
  const { data, error, reload } = useApi<HomeWidgetData>(`${dataPath(widget.kind)}?settings=${queryOf(settings)}`, {
    interval: (kind?.refresh_seconds ?? 900) * 1000,
  });
  const slides = ((data?.view ?? {}) as SlideshowView).slides ?? [];
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(true);
  const count = slides.length;
  const seconds = settings.seconds ?? 12;
  useEffect(() => {
    if (!playing || count < 2) return;
    const timer = window.setInterval(() => setIndex((at) => (at + 1) % count), seconds * 1000);
    return () => window.clearInterval(timer);
  }, [playing, count, seconds]);
  if (!data) return error ? <p className="appw-state appw-error">{error}</p> : <p className="muted">Loading…</p>;
  if (data.state !== 'ok' && !count) {
    const retry = () =>
      void refreshWidget(widget.kind)
        .catch(() => undefined)
        .then(() => reload());
    return <Unavailable data={data} onRetry={retry} onConfigure={onConfigure} takesKey={false} tiny={tiny} />;
  }
  if (!count) return <p className="muted slideshow-empty">{data.empty || 'No photos to show.'}</p>;
  const at = index % count;
  const slide = slides[at];
  // The next photo loads behind this one, so the change is instant.
  const next = slides[(at + 1) % count];
  const caption = [slide.place, takenOn(slide.taken)].filter(Boolean).join(' · ');
  return (
    <div className={`slideshow${settings.fit === 'contain' ? ' is-contain' : ''}`}>
      <img
        key={slide.id}
        className="slideshow-photo"
        src={fileOf(widget.kind, slide.id, settings)}
        alt={caption || 'A photo'}
      />
      {count > 1 && <link rel="prefetch" href={fileOf(widget.kind, next.id, settings)} as="image" />}
      {!tiny && caption && <p className="slideshow-caption">{caption}</p>}
      {count > 1 && (
        <div className="slideshow-controls">
          <button type="button" aria-label="Previous photo" onClick={() => setIndex((at - 1 + count) % count)}>
            <ChevronLeft />
          </button>
          <button
            type="button"
            aria-label={playing ? 'Pause the slideshow' : 'Play the slideshow'}
            onClick={() => setPlaying((value) => !value)}
          >
            {playing ? <Pause /> : <Play />}
          </button>
          <button type="button" aria-label="Next photo" onClick={() => setIndex((at + 1) % count)}>
            <ChevronRight />
          </button>
        </div>
      )}
    </div>
  );
}

export function SlideshowSettings({ widget, onChange }: SettingsProps) {
  const settings = widget.settings as SlideshowSettings;
  const source = settings.source ?? 'memories';
  const { data } = useApi<HomeWidgetData>(`${dataPath(widget.kind)}?settings=${queryOf(settings)}`);
  const albums = ((data?.view ?? {}) as SlideshowView).albums ?? [];
  const set = (patch: SlideshowSettings) => onChange({ ...settings, ...patch });
  return (
    <>
      <label className="widget-setting">
        <span className="widget-setting-label">Photos</span>
        <select
          className="widget-input"
          value={source}
          onChange={(event) => set({ source: event.target.value as Source })}
        >
          {SOURCES.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      </label>
      {source === 'album' && (
        <label className="widget-setting">
          <span className="widget-setting-label">Album</span>
          <select
            className="widget-input"
            value={settings.album_id ?? ''}
            onChange={(event) => set({ album_id: event.target.value })}
          >
            <option value="">Choose one…</option>
            {albums.map((album) => (
              <option key={album.id} value={album.id}>
                {album.name}
              </option>
            ))}
          </select>
          {data && data.state !== 'ok' && <p className="muted widget-setting-hint">{data.detail}</p>}
        </label>
      )}
      <label className="widget-setting">
        <span className="widget-setting-label">Each photo shows for</span>
        <select
          className="widget-input"
          value={settings.seconds ?? 12}
          onChange={(event) => set({ seconds: Number(event.target.value) })}
        >
          {SPEEDS.map((value) => (
            <option key={value} value={value}>
              {value < 60 ? `${value} seconds` : '1 minute'}
            </option>
          ))}
        </select>
      </label>
      <div className="widget-setting">
        <span className="widget-setting-label">Fit</span>
        <div className="segmented" role="radiogroup" aria-label="Fit">
          {(
            [
              ['cover', 'Fill the widget'],
              ['contain', 'Whole photo'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={(settings.fit ?? 'cover') === value}
              onClick={() => set({ fit: value })}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
