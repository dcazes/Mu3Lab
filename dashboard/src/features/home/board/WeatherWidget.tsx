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

export function WeatherSettings({ widget, onChange }: SettingsProps) {
  const settings = widget.settings as { units?: string; place?: PlaceSettings };
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
    </>
  );
}

export function WeatherWidget({ widget, onConfigure }: WidgetProps) {
  const settings = widget.settings as { units?: string; place?: PlaceSettings };
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
  // All seven days; the widget's own width decides how many fit (board.css).
  const days = data.days.slice(0, 7);
  return (
    <div className={`weather weather-h${widget.h}`}>
      <div className="weather-now">
        <Icon className="weather-icon" aria-hidden />
        <div>
          <p className="weather-temp">{degrees(data.temperature)}</p>
          <p className="weather-label">
            {now.label} · {place.name}
          </p>
          {(widget.w >= 2 || widget.h >= 2) && (
            <p className="muted weather-more">
              Feels {degrees(data.feels_like)} · Wind {Math.round(data.wind ?? 0)} {data.wind_unit}
              {data.humidity != null && ` · ${Math.round(data.humidity)}% humidity`}
            </p>
          )}
        </div>
      </div>
      {days.length > 0 && (
        <ol className="weather-days">
          {days.map((day, index) => {
            const DayIcon = look(day.code).day;
            return (
              <li key={day.date} title={look(day.code).label}>
                <span>{weekday(day.date, index)}</span>
                <DayIcon aria-label={look(day.code).label} />
                <span className={`weather-pop${(day.rain_chance ?? 0) >= 30 ? ' is-likely' : ''}`}>
                  <Droplet aria-hidden />
                  <span className="sr-only">Chance of rain </span>
                  {day.rain_chance == null ? '—' : `${Math.round(day.rain_chance)}%`}
                </span>
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
          })}
        </ol>
      )}
    </div>
  );
}
