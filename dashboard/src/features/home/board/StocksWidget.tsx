import { Plus, X } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import type { StockQuote, StocksResponse } from '../../../api';
import { Button } from '../../../components/Button';
import { useApi } from '../../../lib/useApi';
import type { SettingsProps, WidgetProps } from './widgets';

const SYMBOL = /^[A-Za-z0-9.^=-]{1,15}$/;
const MAX = 12;

function symbolsOf(widget: WidgetProps['widget']) {
  return ((widget.settings as { symbols?: string[] }).symbols || []).filter((item) => typeof item === 'string');
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

function Row({ quote, wide }: { quote: StockQuote; wide: boolean }) {
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
      {wide && <Sparkline points={quote.points ?? []} up={up} />}
      <span className="stock-price">{money(quote.price, quote.currency ?? '')}</span>
      <span className={`stock-change ${up ? 'tone-green' : 'tone-red'}`}>
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
  return (
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
  );
}

/** A watchlist from Yahoo Finance: price and today's change, with today's line when there is room. */
export function StocksWidget({ widget, onConfigure }: WidgetProps) {
  const symbols = symbolsOf(widget);
  const { data, error } = useApi<StocksResponse>(
    symbols.length ? `/api/v1/home/stocks?symbols=${encodeURIComponent(symbols.join(','))}` : null,
    { interval: 60_000 },
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
  return (
    <ul className="stocks">
      {(data.quotes ?? []).map((quote) => (
        <Row key={quote.symbol} quote={quote} wide={widget.w >= 2} />
      ))}
    </ul>
  );
}
