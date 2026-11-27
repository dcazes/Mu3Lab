import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudMoon,
  CloudRain,
  CloudSnow,
  CloudSun,
  Droplet,
  type LucideIcon,
  MapPin,
  Moon,
  Search,
  Sun,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, type Place, type PlacesResponse, type WeatherResponse } from '../../../api';
import { errorText } from '../../../lib/format';
import { useApi } from '../../../lib/useApi';
import { Button } from '../../../components/Button';
import { FitList, useBox } from './size';
import type { SettingsProps, WidgetProps } from './widgets';

interface Look {
  label: string;
  day: LucideIcon;
  night: LucideIcon;
}

// WMO weather codes, as Open-Meteo reports them.
function look(code: number): Look {
  if (code === 0) return { label: 'Clear', day: Sun, night: Moon };
  if (code <= 2) return { label: code === 1 ? 'Mostly clear' : 'Partly cloudy', day: CloudSun, night: CloudMoon };
  if (code === 3) return { label: 'Cloudy', day: Cloud, night: Cloud };
  if (code <= 48) return { label: 'Fog', day: CloudFog, night: CloudFog };
  if (code <= 57) return { label: 'Drizzle', day: CloudDrizzle, night: CloudDrizzle };
  if (code <= 67 || (code >= 80 && code <= 82)) return { label: 'Rain', day: CloudRain, night: CloudRain };
  if (code <= 77 || code === 85 || code === 86) return { label: 'Snow', day: CloudSnow, night: CloudSnow };
  return { label: 'Thunderstorms', day: CloudLightning, night: CloudLightning };
}

const degrees = (value: number | null | undefined) => (value == null ? '—' : `${Math.round(value)}°`);

function weekday(date: string, index: number) {
  if (index === 0) return 'Today';
  return new Date(`${date}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short' });
}

interface PlaceSettings {
  name: string;
  latitude: number;
  longitude: number;
}

/** Search for a town and pick it; used in the widget's settings and when it has no place yet. */
export function PlacePicker({ onPick }: { onPick: (place: PlaceSettings) => void }) {
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<Place[] | null>(null);
  const [error, setError] = useState('');
  const searching = query.trim().length >= 2;
  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<PlacesResponse>(`/api/v1/home/places?q=${encodeURIComponent(term)}`, controller.signal)
        .then((result) => {
          setFound(result.places);
          setError('');
        })
        .catch((cause) => {
          if (!controller.signal.aborted) setError(errorText(cause));
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query]);
  return (
    <div className="place-picker">
      <label className="search-input">
        <Search />
        <input
          type="search"
          aria-label="Town or city"
          placeholder="Town or city"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {error && <p className="widget-error">{error}</p>}
      {searching && found && !found.length && <p className="muted">No place by that name.</p>}
      {searching && found && found.length > 0 && (
        <ul className="place-results">
          {found.map((place) => (
            <li key={`${place.latitude},${place.longitude}`}>
              <button
                type="button"
                onClick={() => onPick({ name: place.name, latitude: place.latitude, longitude: place.longitude })}
              >
                <MapPin />
                <span>
                  {place.name}
                  {place.region && <small>{place.region}</small>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface WeatherSettingsValue {
  units?: string;
  days?: number;
  place?: PlaceSettings;
}

// How many days the forecast can show (the server keeps two weeks).
const DAY_CHOICES = [3, 5, 7, 10, 14];
const daysOf = (settings: WeatherSettingsValue) =>
  DAY_CHOICES.includes(Number(settings.days)) ? Number(settings.days) : 7;

export function WeatherSettings({ widget, onChange }: SettingsProps) {
  const settings = widget.settings as WeatherSettingsValue;
  return (
    <>
      <div className="widget-setting">
        <span className="widget-setting-label">Place</span>
        {settings.place && (
          <p className="widget-setting-value">
            <MapPin /> {settings.place.name}
          </p>
        )}
        <PlacePicker onPick={(place) => onChange({ ...settings, place })} />
      </div>
      <div className="widget-setting">
        <span className="widget-setting-label">Units</span>
        <div className="segmented" role="radiogroup" aria-label="Units">
          {(['celsius', 'fahrenheit'] as const).map((units) => (
            <button
              key={units}
              type="button"
              role="radio"
              aria-checked={(settings.units || 'celsius') === units}
              onClick={() => onChange({ ...settings, units })}
            >
              {units === 'celsius' ? '°C' : '°F'}
            </button>
          ))}
        </div>
      </div>
      <div className="widget-setting">
        <span className="widget-setting-label">Days</span>
        <div className="segmented" role="radiogroup" aria-label="Days in the forecast">
          {DAY_CHOICES.map((days) => (
            <button
              key={days}
              type="button"
              role="radio"
              aria-checked={daysOf(settings) === days}
              onClick={() => onChange({ ...settings, days })}
            >
              {days}
            </button>
          ))}
        </div>
        <p className="muted widget-setting-hint">At most this many; a small widget shows the days that fit.</p>
      </div>
    </>
  );
}

export function WeatherWidget({ widget, onConfigure }: WidgetProps) {
  const box = useBox();
  const settings = widget.settings as WeatherSettingsValue;
  const place = settings.place;
  const path = place
    ? `/api/v1/home/weather?latitude=${place.latitude}&longitude=${place.longitude}&units=${settings.units || 'celsius'}`
    : null;
  const { data, error } = useApi<WeatherResponse>(path, { interval: 10 * 60_000 });
  if (!place)
    return (
      <div className="widget-empty">
        <p>Choose a place for the forecast.</p>
        <Button variant="secondary" size="sm" icon={MapPin} onClick={onConfigure}>
          Choose a place
        </Button>
      </div>
    );
  if (!data) return error ? <p className="widget-error">{error}</p> : <p className="muted">Loading…</p>;
  const now = look(data.code);
  const Icon = data.is_day ? now.day : now.night;
  const days = data.days.slice(0, daysOf(settings));
  // The week's coldest and warmest, for each day's bar in the list.
  const lows = days.map((day) => day.low).filter((value): value is number => value != null);
  const highs = days.map((day) => day.high).filter((value): value is number => value != null);
  const week: [number, number] | null = lows.length && highs.length ? [Math.min(...lows), Math.max(...highs)] : null;
  // The layout follows the room: "now" beside the days when short, "now" above a row of days, or
  // above a list of them once tall. Days that do not fit are left out whole.
  const { width, height } = box;
  const measured = width > 0;
  const layout = !measured ? 'stack' : height < 140 ? 'row' : height >= 200 ? 'list' : 'stack';
  const big = measured && height >= 200 && width >= 400;
  const showDays = days.length > 0 && (layout !== 'row' || width >= 300);
  const details = layout === 'row' ? width >= 640 : width >= 280;
  return (
    <div className={`weather weather-${layout}${big ? ' is-big' : ''}`}>
      <div className="weather-now">
        <Icon className="weather-icon" aria-hidden />
        <p className="weather-temp">{degrees(data.temperature)}</p>
        <div className="weather-text">
          <p className="weather-label">
            {now.label} · {place.name}
          </p>
          {details && (
            <p className="muted weather-more">
              Feels {degrees(data.feels_like)} · Wind {Math.round(data.wind ?? 0)} {data.wind_unit}
              {data.humidity != null && ` · ${Math.round(data.humidity)}% humidity`}
            </p>
          )}
        </div>
      </div>
      {showDays && layout === 'list' && (
        <FitList className="weather-list" label="Forecast" more={(hidden) => `+${hidden} more days`}>
          {days.map((day, index) => (
            <Day key={day.date} day={day} index={index} week={week} />
          ))}
        </FitList>
      )}
      {showDays && layout !== 'list' && (
        <ol className="weather-days" aria-label="Forecast">
          {days.map((day, index) => (
            <Day key={day.date} day={day} index={index} />
          ))}
        </ol>
      )}
    </div>
  );
}

function Day({
  day,
  index,
  week,
}: {
  day: WeatherResponse['days'][number];
  index: number;
  /** In the list: the week's range, to draw where this day sits in it. */
  week?: [number, number] | null;
}) {
  const DayIcon = look(day.code).day;
  const span = week && day.low != null && day.high != null ? week[1] - week[0] || 1 : 0;
  return (
    <li title={look(day.code).label}>
      <span className="weather-day">{weekday(day.date, index)}</span>
      <DayIcon aria-label={look(day.code).label} />
      <span className={`weather-pop${(day.rain_chance ?? 0) >= 30 ? ' is-likely' : ''}`}>
        <Droplet aria-hidden />
        <span className="sr-only">Chance of rain </span>
        {day.rain_chance == null ? '—' : `${Math.round(day.rain_chance)}%`}
      </span>
      {week && span > 0 && (
        <span className="weather-bar" aria-hidden>
          <i
            style={{
              left: `${(((day.low ?? 0) - week[0]) / span) * 100}%`,
              right: `${((week[1] - (day.high ?? 0)) / span) * 100}%`,
            }}
          />
        </span>
      )}
      <span className="weather-range">
        <span className="sr-only">High </span>
        {degrees(day.high)}
        <small>
          <span className="sr-only">Low </span>
          {degrees(day.low)}
        </small>
      </span>
    </li>
  );
}
