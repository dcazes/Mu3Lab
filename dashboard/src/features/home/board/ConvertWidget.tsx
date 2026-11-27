import { ArrowLeftRight } from 'lucide-react';
import { useState } from 'react';
import type { RatesResponse } from '../../../api';
import { useApi } from '../../../lib/useApi';
import { useBox } from './size';
import type { SettingsProps, WidgetProps } from './widgets';

type Kind = 'length' | 'weight' | 'temperature' | 'volume' | 'cooking' | 'speed' | 'area' | 'data' | 'currency';

interface ConvertSettings {
  kind?: Kind;
  from?: string;
  to?: string;
}

/** Each unit as how many of the kind's base unit it is (temperature is converted on its own). */
const UNITS: Record<Exclude<Kind, 'currency' | 'temperature'>, [string, string, number][]> = {
  length: [
    ['mm', 'Millimetres', 0.001],
    ['cm', 'Centimetres', 0.01],
    ['m', 'Metres', 1],
    ['km', 'Kilometres', 1000],
    ['in', 'Inches', 0.0254],
    ['ft', 'Feet', 0.3048],
    ['yd', 'Yards', 0.9144],
    ['mi', 'Miles', 1609.344],
  ],
  weight: [
    ['g', 'Grams', 1],
    ['kg', 'Kilograms', 1000],
    ['oz', 'Ounces', 28.349523125],
    ['lb', 'Pounds', 453.59237],
    ['st', 'Stone', 6350.29318],
    ['t', 'Tonnes', 1_000_000],
  ],
  volume: [
    ['ml', 'Millilitres', 0.001],
    ['l', 'Litres', 1],
    ['floz', 'US fluid ounces', 0.0295735295625],
    ['pt', 'US pints', 0.473176473],
    ['qt', 'US quarts', 0.946352946],
    ['gal', 'US gallons', 3.785411784],
    ['impgal', 'Imperial gallons', 4.54609],
  ],
  cooking: [
    ['tsp', 'Teaspoons', 0.00492892159375],
    ['tbsp', 'Tablespoons', 0.01478676478125],
    ['cup', 'US cups', 0.2365882365],
    ['mcup', 'Metric cups', 0.25],
    ['ml', 'Millilitres', 0.001],
    ['l', 'Litres', 1],
    ['floz', 'US fluid ounces', 0.0295735295625],
  ],
  speed: [
    ['kmh', 'km/h', 1 / 3.6],
    ['mph', 'mph', 0.44704],
    ['ms', 'm/s', 1],
    ['kn', 'Knots', 0.514444],
  ],
  area: [
    ['m2', 'Square metres', 1],
    ['km2', 'Square kilometres', 1_000_000],
    ['ft2', 'Square feet', 0.09290304],
    ['ac', 'Acres', 4046.8564224],
    ['ha', 'Hectares', 10_000],
  ],
  data: [
    ['kb', 'Kilobytes', 1e3],
    ['mb', 'Megabytes', 1e6],
    ['gb', 'Gigabytes', 1e9],
    ['tb', 'Terabytes', 1e12],
    ['gib', 'Gibibytes', 2 ** 30],
    ['tib', 'Tebibytes', 2 ** 40],
  ],
};
const TEMPERATURE: [string, string][] = [
  ['c', '°C'],
  ['f', '°F'],
  ['k', 'Kelvin'],
];
const KINDS: [Kind, string][] = [
  ['length', 'Length'],
  ['weight', 'Weight'],
  ['temperature', 'Temperature'],
  ['volume', 'Volume'],
  ['cooking', 'Cooking'],
  ['speed', 'Speed'],
  ['area', 'Area'],
  ['data', 'Data'],
  ['currency', 'Currency'],
];
const DEFAULTS: Record<Kind, [string, string]> = {
  length: ['km', 'mi'],
  weight: ['kg', 'lb'],
  temperature: ['c', 'f'],
  volume: ['l', 'gal'],
  cooking: ['cup', 'ml'],
  speed: ['kmh', 'mph'],
  area: ['m2', 'ft2'],
  data: ['gb', 'gib'],
  currency: ['USD', 'EUR'],
};

function toCelsius(value: number, unit: string) {
  return unit === 'f' ? ((value - 32) * 5) / 9 : unit === 'k' ? value - 273.15 : value;
}
function fromCelsius(value: number, unit: string) {
  return unit === 'f' ? (value * 9) / 5 + 32 : unit === 'k' ? value + 273.15 : value;
}

/** A number as people read it: a few significant digits, no float noise. */
function shown(value: number) {
  if (!Number.isFinite(value)) return '—';
  const digits = Math.abs(value) >= 1000 ? 2 : Math.abs(value) >= 1 ? 4 : 6;
  return value.toLocaleString(undefined, { maximumFractionDigits: digits, maximumSignificantDigits: 10 });
}

function unitsOf(kind: Kind, rates?: RatesResponse | null): [string, string][] {
  if (kind === 'temperature') return TEMPERATURE;
  if (kind === 'currency') {
    const codes = rates ? [rates.base, ...Object.keys(rates.rates)] : DEFAULTS.currency;
    return [...new Set(codes)].sort().map((code) => [code, code]);
  }
  return UNITS[kind].map(([id, name]) => [id, name]);
}

/** A quick conversion: an amount, from one unit to another; currency at today's reference rates. */
export function ConvertWidget({ widget }: WidgetProps) {
  const settings = widget.settings as ConvertSettings;
  const { width } = useBox();
  const [kind, setKind] = useState<Kind>(settings.kind ?? 'length');
  const [from, setFrom] = useState(settings.from ?? DEFAULTS[settings.kind ?? 'length'][0]);
  const [to, setTo] = useState(settings.to ?? DEFAULTS[settings.kind ?? 'length'][1]);
  const [amount, setAmount] = useState('1');
  // Rates only while converting money: nothing is asked of the outside otherwise.
  const { data: rates, error } = useApi<RatesResponse>(
    kind === 'currency' ? `/api/v1/home/rates?base=${encodeURIComponent(from)}` : null,
  );
  const choose = (next: Kind) => {
    setKind(next);
    setFrom(DEFAULTS[next][0]);
    setTo(DEFAULTS[next][1]);
  };
  const value = Number(amount.replace(',', '.'));
  let result = NaN;
  if (Number.isFinite(value)) {
    if (kind === 'temperature') result = fromCelsius(toCelsius(value, from), to);
    else if (kind === 'currency') result = from === to ? value : rates?.rates[to] ? value * rates.rates[to] : NaN;
    else {
      const table = UNITS[kind];
      const a = table.find(([id]) => id === from)?.[2];
      const b = table.find(([id]) => id === to)?.[2];
      if (a && b) result = (value * a) / b;
    }
  }
  const units = unitsOf(kind, rates);
  const { height } = useBox();
  const narrow = width > 0 && width < 300;
  const roomy = height === 0 || height >= 150;
  const pick = (value: string, set: (unit: string) => void, label: string) => (
    <select className="widget-input" aria-label={label} value={value} onChange={(event) => set(event.target.value)}>
      {units.map(([id, name]) => (
        <option key={id} value={id}>
          {name}
        </option>
      ))}
    </select>
  );
  // What to convert, with the swap beside it; the amount and its unit; the answer and its unit.
  return (
    <div className={`convert${narrow ? ' is-narrow' : ''}`}>
      <div className="convert-row">
        <select
          className="widget-input convert-kind"
          aria-label="What to convert"
          value={kind}
          onChange={(e) => choose(e.target.value as Kind)}
        >
          {KINDS.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="widget-tool convert-swap"
          aria-label="Swap the units"
          title="Swap the units"
          onClick={() => {
            setFrom(to);
            setTo(from);
          }}
        >
          <ArrowLeftRight />
        </button>
      </div>
      <div className="convert-row">
        <input
          className="widget-input convert-amount"
          inputMode="decimal"
          aria-label="Amount"
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
        />
        {pick(from, setFrom, 'From')}
      </div>
      <div className="convert-row">
        <output className="convert-result" aria-live="polite" title={shown(result)}>
          {kind === 'currency' && error && !rates ? <span className="widget-error">{error}</span> : shown(result)}
        </output>
        {pick(to, setTo, 'To')}
      </div>
      {roomy && kind === 'currency' && rates?.date && (
        <p className="muted convert-note">European Central Bank rate of {rates.date}</p>
      )}
    </div>
  );
}

export function ConvertSettings({ widget, onChange }: SettingsProps) {
  const settings = widget.settings as ConvertSettings;
  const kind = settings.kind ?? 'length';
  const units = unitsOf(kind);
  const [from, to] = [settings.from ?? DEFAULTS[kind][0], settings.to ?? DEFAULTS[kind][1]];
  return (
    <>
      <label className="widget-setting">
        <span className="widget-setting-label">Opens on</span>
        <select
          className="widget-input"
          value={kind}
          onChange={(event) => {
            const next = event.target.value as Kind;
            onChange({ kind: next, from: DEFAULTS[next][0], to: DEFAULTS[next][1] });
          }}
        >
          {KINDS.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      </label>
      {kind === 'currency' ? (
        <div className="widget-setting">
          <span className="widget-setting-label">Currencies</span>
          <div className="inline-form">
            {(['from', 'to'] as const).map((key) => (
              <input
                key={key}
                className="widget-input"
                aria-label={key === 'from' ? 'From currency' : 'To currency'}
                maxLength={3}
                value={key === 'from' ? from : to}
                onChange={(event) =>
                  onChange({ ...settings, kind, [key]: event.target.value.toUpperCase().replace(/[^A-Z]/g, '') })
                }
              />
            ))}
          </div>
          <p className="muted widget-setting-hint">Three-letter codes, like CAD, USD, EUR or GBP.</p>
        </div>
      ) : (
        <div className="widget-setting">
          <span className="widget-setting-label">Units</span>
          <div className="inline-form">
            {(['from', 'to'] as const).map((key) => (
              <select
                key={key}
                className="widget-input"
                aria-label={key === 'from' ? 'From' : 'To'}
                value={key === 'from' ? from : to}
                onChange={(event) => onChange({ ...settings, kind, [key]: event.target.value })}
              >
                {units.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
            ))}
          </div>
        </div>
      )}
      <p className="muted widget-setting-hint">
        You can switch what it converts right in the widget too; this is where it starts.
      </p>
    </>
  );
}
