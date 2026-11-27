import { Plus, X } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import type { StockQuote, StocksResponse } from '../../../api';
import { Button } from '../../../components/Button';
import { useApi } from '../../../lib/useApi';
import { FitList, useBox } from './size';
import type { SettingsProps, WidgetProps } from './widgets';

const SYMBOL = /^[A-Za-z0-9.^=-]{1,15}$/;
const MAX = 12;

function symbolsOf(widget: WidgetProps['widget']) {
  return ((widget.settings as { symbols?: string[] }).symbols || []).filter((item) => typeof item === 'string');
}

/** How far back the change and the line look: Yahoo's range, and what it is compared with. */
const RANGES = [
  ['1d', 'Yesterday’s close'],
  ['5d', '5 days ago'],
  ['1mo', '1 month ago'],
  ['6mo', '6 months ago'],
  ['ytd', 'January 1'],
  ['1y', '1 year ago'],
  ['5y', '5 years ago'],
] as const;
type Range = (typeof RANGES)[number][0];

function rangeOf(widget: WidgetProps['widget']): Range {
  const value = (widget.settings as { range?: string }).range;
  return RANGES.find(([id]) => id === value)?.[0] ?? '1d';
}

function money(value: number | null | undefined, currency: string) {
  if (value == null) return '—';
  try {
    return value.toLocaleString(undefined, { style: 'currency', currency: currency || 'USD' });
  } catch {
    return value.toFixed(2);
  }
}

function Sparkline({ points, up }: { points: number[]; up: boolean }) {
  if (points.length < 2) return <span className="sparkline" />;
  const low = Math.min(...points);
  const span = Math.max(...points) - low || 1;
  const line = points
    .map(
      (value, index) =>
        `${((index / (points.length - 1)) * 100).toFixed(1)},${(30 - ((value - low) / span) * 28 - 1).toFixed(1)}`,
    )
    .join(' ');
  return (
    <svg
      className={`sparkline ${up ? 'tone-green' : 'tone-red'}`}
      viewBox="0 0 100 30"
      preserveAspectRatio="none"
      aria-hidden
    >
      <polyline points={line} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Row({
  quote,
  wide,
  showPrice,
  showLine,
  since,
}: {
  quote: StockQuote;
  wide: boolean;
  showPrice: boolean;
  showLine: boolean;
  since: string;
}) {
  if (!quote.found)
    return (
      <li className="stock">
        <span className="stock-symbol">{quote.symbol}</span>
        <span className="muted stock-missing">Not found</span>
      </li>
    );
  const up = (quote.change ?? 0) >= 0;
  return (
    <li className="stock" title={quote.name}>
      <span className="stock-symbol">
        {quote.symbol}
        {wide && <small>{quote.name}</small>}
      </span>
      {showLine && <Sparkline points={quote.points ?? []} up={up} />}
      {showPrice && <span className="stock-price">{money(quote.price, quote.currency ?? '')}</span>}
      <span className={`stock-change ${up ? 'tone-green' : 'tone-red'}`} title={`Compared with ${since.toLowerCase()}`}>
        {quote.change_percent == null ? '—' : `${up ? '+' : ''}${quote.change_percent.toFixed(2)}%`}
      </span>
    </li>
  );
}

export function StocksSettings({ widget, onChange }: SettingsProps) {
  const symbols = symbolsOf(widget);
  const [value, setValue] = useState('');
  const term = value.trim().toUpperCase();
  const valid = SYMBOL.test(term) && !symbols.includes(term) && symbols.length < MAX;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    onChange({ ...widget.settings, symbols: [...symbols, term] });
    setValue('');
  };
  const range = rangeOf(widget);
  return (
    <>
      <div className="widget-setting">
        <span className="widget-setting-label">Compare with</span>
        <select
          className="widget-input"
          aria-label="Compare with"
          value={range}
          onChange={(event) => onChange({ ...widget.settings, range: event.target.value })}
        >
          {RANGES.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
        <p className="muted widget-setting-hint">The change and the line cover this stretch.</p>
      </div>
      <div className="widget-setting">
        <span className="widget-setting-label">Tickers</span>
        {symbols.length > 0 && (
          <ul className="chip-list">
            {symbols.map((symbol) => (
              <li key={symbol}>
                {symbol}
                <button
                  type="button"
                  aria-label={`Remove ${symbol}`}
                  onClick={() => onChange({ ...widget.settings, symbols: symbols.filter((item) => item !== symbol) })}
                >
                  <X />
                </button>
              </li>
            ))}
          </ul>
        )}
        <form className="inline-form" onSubmit={submit}>
          <input
            className="widget-input"
            aria-label="Ticker symbol"
            placeholder="AAPL, VFV.TO, BTC-USD"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
          <Button type="submit" variant="secondary" size="sm" icon={Plus} disabled={!valid}>
            Add
          </Button>
        </form>
        <p className="muted widget-setting-hint">
          Yahoo Finance symbols: add the exchange for stocks outside the US (.TO for Toronto, .L for London). Prices
          update every minute while Home is open and may be delayed.
        </p>
      </div>
    </>
  );
}

/** A watchlist from Yahoo Finance: price, the change over the chosen range, and its line. */
export function StocksWidget({ widget, onConfigure }: WidgetProps) {
  const { width } = useBox();
  const symbols = symbolsOf(widget);
  const range = rangeOf(widget);
  const { data, error } = useApi<StocksResponse>(
    symbols.length ? `/api/v1/home/stocks?symbols=${encodeURIComponent(symbols.join(','))}&range=${range}` : null,
    { interval: range === '1d' ? 60_000 : 5 * 60_000 },
  );
  if (!symbols.length)
    return (
      <div className="widget-empty">
        <p>Follow a few tickers here.</p>
        <Button variant="secondary" size="sm" icon={Plus} onClick={onConfigure}>
          Add tickers
        </Button>
      </div>
    );
  if (!data) return error ? <p className="widget-error">{error}</p> : <p className="muted">Loading…</p>;
  // Always the ticker, its line and the change (the line shrinks first). Then the price; wide, the name.
  const measured = width > 0;
  const wide = !measured || width >= 480;
  const showPrice = !measured || width >= 290;
  const showLine = !measured || width >= 140;
  const since = RANGES.find(([id]) => id === range)?.[1] ?? '';
  return (
    <FitList
      className={`stocks${wide ? ' is-wide' : showPrice ? '' : ' is-narrow'}${showLine ? '' : ' no-line'}`}
      label={`Watchlist, compared with ${since.toLowerCase()}`}
      more={(hidden) => `+${hidden} more`}
    >
      {(data.quotes ?? []).map((quote) => (
        <Row key={quote.symbol} quote={quote} wide={wide} showPrice={showPrice} showLine={showLine} since={since} />
      ))}
    </FitList>
  );
}
