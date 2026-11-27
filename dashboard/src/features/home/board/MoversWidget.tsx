import { Plus, X } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import type { Mover, MoversResponse } from '../../../api';
import { Button } from '../../../components/Button';
import { useApi } from '../../../lib/useApi';
import type { SettingsProps, WidgetProps } from './widgets';

type List = 'gainers' | 'losers' | 'active' | 'mine';

interface MoversSettings {
  list?: List;
  symbols?: string[];
  still?: boolean;
}

const LISTS: [List, string][] = [
  ['gainers', 'Top gainers'],
  ['losers', 'Top losers'],
  ['active', 'Most traded'],
  ['mine', 'My tickers'],
];
const SYMBOL = /^[A-Za-z0-9.^=-]{1,15}$/;
// Yahoo's lists move through the day; the server keeps them five minutes.
const EVERY = 5 * 60_000;

function price(value: number | null | undefined, currency: string) {
  if (value == null) return '';
  try {
    return value.toLocaleString(undefined, { style: 'currency', currency: currency || 'USD' });
  } catch {
    return value.toFixed(2);
  }
}

function Quote({ mover }: { mover: Mover }) {
  const change = mover.change_percent ?? 0;
  return (
    <li className="mover" title={mover.name}>
      <strong>{mover.symbol}</strong>
      <span className="muted">{price(mover.price, mover.currency ?? '')}</span>
      <span className={change >= 0 ? 'tone-green' : 'tone-red'}>
        {change >= 0 ? '▲' : '▼'} {Math.abs(change).toFixed(2)}%
      </span>
    </li>
  );
}

/** The day's biggest movers (or the person's own tickers), running along the board like a ticker. */
export function MoversWidget({ widget, onConfigure }: WidgetProps) {
  const { list = 'gainers', symbols = [], still = false } = widget.settings as MoversSettings;
  const mine = list === 'mine';
  const query = mine ? `list=mine&symbols=${encodeURIComponent(symbols.join(','))}` : `list=${list}`;
  const { data, error } = useApi<MoversResponse>(mine && !symbols.length ? null : `/api/v1/home/movers?${query}`, {
    interval: EVERY,
  });
  if (mine && !symbols.length)
    return (
      <div className="widget-empty">
        <p>Add the tickers to run along here.</p>
        <Button variant="secondary" size="sm" icon={Plus} onClick={onConfigure}>
          Add tickers
        </Button>
      </div>
    );
  if (!data) return error ? <p className="widget-error">{error}</p> : <p className="muted">Loading…</p>;
  if (!data.movers.length) return <p className="muted">Nothing to show right now.</p>;
  // The list twice, so the second copy follows the first without a gap as it runs.
  return (
    <div className={`movers${still ? ' is-still' : ''}`} aria-label={LISTS.find(([id]) => id === list)?.[1]}>
      <ul className="movers-track" style={{ '--movers-count': data.movers.length } as React.CSSProperties}>
        {data.movers.map((mover) => (
          <Quote key={mover.symbol} mover={mover} />
        ))}
        {!still &&
          data.movers.map((mover) => (
            <li key={`again-${mover.symbol}`} className="mover-copy" aria-hidden>
              <strong>{mover.symbol}</strong>
              <span className="muted">{price(mover.price, mover.currency ?? '')}</span>
              <span className={(mover.change_percent ?? 0) >= 0 ? 'tone-green' : 'tone-red'}>
                {(mover.change_percent ?? 0) >= 0 ? '▲' : '▼'} {Math.abs(mover.change_percent ?? 0).toFixed(2)}%
              </span>
            </li>
          ))}
      </ul>
    </div>
  );
}

export function MoversSettings({ widget, onChange }: SettingsProps) {
  const settings = widget.settings as MoversSettings;
  const list = settings.list ?? 'gainers';
  const symbols = settings.symbols ?? [];
  const [value, setValue] = useState('');
  const term = value.trim().toUpperCase();
  const valid = SYMBOL.test(term) && !symbols.includes(term) && symbols.length < 24;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    onChange({ ...settings, symbols: [...symbols, term] });
    setValue('');
  };
  return (
    <>
      <label className="widget-setting">
        <span className="widget-setting-label">Shows</span>
        <select
          className="widget-input"
          value={list}
          onChange={(event) => onChange({ ...settings, list: event.target.value })}
        >
          {LISTS.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
        <p className="muted widget-setting-hint">
          Gainers, losers and most traded are Yahoo Finance’s US lists for today.
        </p>
      </label>
      {list === 'mine' && (
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
                    onClick={() => onChange({ ...settings, symbols: symbols.filter((item) => item !== symbol) })}
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
        </div>
      )}
      <label className="board-panel-switch">
        <input
          type="checkbox"
          checked={settings.still === true}
          onChange={(event) => onChange({ ...settings, still: event.target.checked })}
        />
        <span>
          Keep it still
          <small>Show the list without running it along. A device set to reduce motion always does.</small>
        </span>
      </label>
    </>
  );
}
