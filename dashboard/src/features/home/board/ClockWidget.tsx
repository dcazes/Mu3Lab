import { Plus, X } from 'lucide-react';
import { type FormEvent, useEffect, useState } from 'react';
import { Button } from '../../../components/Button';
import { useBox } from './size';
import type { SettingsProps, WidgetProps } from './widgets';

interface Zone {
  zone: string;
  label: string;
}

interface ClockSettings {
  zones?: Zone[];
  local?: boolean;
  hours?: 'auto' | '12' | '24';
  seconds?: boolean;
  date?: boolean;
}

const MAX = 6;
const settingsOf = (widget: WidgetProps['widget']) => widget.settings as ClockSettings;

/** The time now, ticking each second (or each minute when seconds are not shown). */
function useNow(seconds: boolean) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const step = seconds ? 1000 : 60_000;
    let timer = 0;
    const tick = () => {
      setNow(new Date());
      // On the second (or minute), so the clock never shows a stale time for most of a step.
      timer = window.setTimeout(tick, step - (Date.now() % step));
    };
    timer = window.setTimeout(tick, step - (Date.now() % step));
    return () => window.clearTimeout(timer);
  }, [seconds]);
  return now;
}

/** "Toronto" from "America/Toronto". */
const cityOf = (zone: string) => zone.split('/').pop()?.replace(/_/g, ' ') ?? zone;

function hour12(hours: ClockSettings['hours']) {
  return hours === '12' ? true : hours === '24' ? false : undefined;
}

/** The device's own time first (unless left out), then each place the person added. */
export function ClockWidget({ widget }: WidgetProps) {
  const settings = settingsOf(widget);
  const { width, height } = useBox();
  const now = useNow(settings.seconds === true);
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const clocks: Zone[] = [
    ...(settings.local !== false ? [{ zone: here, label: '' }] : []),
    ...(settings.zones ?? []).filter((item) => item.zone !== here || settings.local === false),
  ];
  if (!clocks.length) return <p className="muted">Add a place in this widget’s settings.</p>;
  const [first, ...others] = clocks;
  const time = (zone: string) =>
    now.toLocaleTimeString(undefined, {
      timeZone: zone,
      hour: 'numeric',
      minute: '2-digit',
      second: settings.seconds ? '2-digit' : undefined,
      hour12: hour12(settings.hours),
    });
  const day = (zone: string) =>
    now.toLocaleDateString(undefined, { timeZone: zone, weekday: 'long', month: 'long', day: 'numeric' });
  // Hours ahead or behind the first clock, for the smaller ones ("+5 h").
  const offset = (zone: string) => {
    const at = (name: string) => new Date(now.toLocaleString('en-US', { timeZone: name })).getTime();
    const hours = Math.round(((at(zone) - at(first.zone)) / 3_600_000) * 2) / 2;
    return hours === 0 ? 'Same time' : `${hours > 0 ? '+' : ''}${hours} h`;
  };
  // Big when there is room; the other places in a row below, or beside it when wide and short. Short:
  // each other place on one line, and the date only when it still fits.
  const measured = width > 0;
  const big = !measured || (height >= 120 && width >= 220);
  const beside = measured && height < 150 && width >= 480 && others.length > 0;
  const short = measured && !beside && height < 120;
  const showDate = settings.date !== false && !(short && others.length > 0 && height < 90);
  const shownOthers = measured
    ? others.slice(0, Math.max(0, Math.floor((beside ? width - 240 : width) / (short ? 150 : 120))))
    : others;
  return (
    <div className={`clock${big ? ' is-big' : ''}${beside ? ' is-beside' : ''}${short ? ' is-short' : ''}`}>
      <div className="clock-main">
        <p className="clock-time">{time(first.zone)}</p>
        {(showDate || first.label || first.zone !== here) && (
          <p className="clock-place muted">
            {first.label || (first.zone === here ? '' : cityOf(first.zone))}
            {showDate && (
              <span>
                {first.label || first.zone !== here ? ' · ' : ''}
                {day(first.zone)}
              </span>
            )}
          </p>
        )}
      </div>
      {shownOthers.length > 0 && (
        <ul className="clock-others">
          {shownOthers.map((item) => (
            <li key={item.zone} title={`${item.label || cityOf(item.zone)}: ${offset(item.zone)}`}>
              <span className="clock-other-name">{item.label || cityOf(item.zone)}</span>
              <strong>{time(item.zone)}</strong>
              {!short && <small className="muted">{offset(item.zone)}</small>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Every time zone the browser knows ("America/Toronto"), read once.
let knownZones: string[] | null = null;
function timeZones(): string[] {
  if (knownZones === null)
    try {
      knownZones =
        (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.('timeZone') ?? [];
    } catch {
      knownZones = [];
    }
  return knownZones ?? [];
}

export function ClockSettings({ widget, onChange }: SettingsProps) {
  const settings = settingsOf(widget);
  const zones = settings.zones ?? [];
  const every = timeZones();
  const [query, setQuery] = useState('');
  const wanted = query.trim().toLowerCase().replace(/\s+/g, '_');
  const matches = wanted
    ? every
        .filter((zone) => zone.toLowerCase().includes(wanted) && !zones.some((item) => item.zone === zone))
        .slice(0, 6)
    : [];
  const set = (patch: ClockSettings) => onChange({ ...settings, ...patch });
  const add = (zone: string) => {
    if (zones.length >= MAX) return;
    set({ zones: [...zones, { zone, label: '' }] });
    setQuery('');
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (matches[0]) add(matches[0]);
  };
  return (
    <>
      <label className="board-panel-switch">
        <input type="checkbox" checked={settings.local !== false} onChange={(e) => set({ local: e.target.checked })} />
        <span>This device’s time first</span>
      </label>
      <div className="widget-setting">
        <span className="widget-setting-label">Other places</span>
        {zones.length > 0 && (
          <ul className="clock-zone-list">
            {zones.map((item) => (
              <li key={item.zone}>
                <input
                  className="widget-input"
                  aria-label={`Name for ${cityOf(item.zone)}`}
                  placeholder={cityOf(item.zone)}
                  maxLength={30}
                  value={item.label}
                  onChange={(event) =>
                    set({
                      zones: zones.map((each) =>
                        each.zone === item.zone ? { ...each, label: event.target.value } : each,
                      ),
                    })
                  }
                />
                <button
                  type="button"
                  className="widget-tool"
                  aria-label={`Remove ${cityOf(item.zone)}`}
                  onClick={() => set({ zones: zones.filter((each) => each.zone !== item.zone) })}
                >
                  <X />
                </button>
              </li>
            ))}
          </ul>
        )}
        {zones.length < MAX && (
          <form className="inline-form" onSubmit={submit}>
            <input
              className="widget-input"
              aria-label="Find a city"
              placeholder="City, like Tokyo or London"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Button type="submit" variant="secondary" size="sm" icon={Plus} disabled={!matches.length}>
              Add
            </Button>
          </form>
        )}
        {matches.length > 0 && (
          <ul className="place-results">
            {matches.map((zone) => (
              <li key={zone}>
                <button type="button" onClick={() => add(zone)}>
                  {cityOf(zone)} <small className="muted">{zone}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="widget-setting">
        <span className="widget-setting-label">Hours</span>
        <div className="segmented" role="radiogroup" aria-label="Hours">
          {(
            [
              ['auto', 'As this device'],
              ['12', '12 h'],
              ['24', '24 h'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={(settings.hours ?? 'auto') === value}
              onClick={() => set({ hours: value })}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <label className="board-panel-switch">
        <input
          type="checkbox"
          checked={settings.seconds === true}
          onChange={(e) => set({ seconds: e.target.checked })}
        />
        <span>Seconds</span>
      </label>
      <label className="board-panel-switch">
        <input type="checkbox" checked={settings.date !== false} onChange={(e) => set({ date: e.target.checked })} />
        <span>Today’s date</span>
      </label>
    </>
  );
}
