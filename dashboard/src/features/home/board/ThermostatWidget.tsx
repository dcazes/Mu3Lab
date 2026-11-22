import { Droplets, Flame, Minus, Plus, Snowflake } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { postJsonApi, type ThermostatStatus } from '../../../api';
import { errorText } from '../../../lib/format';
import { Link } from '../../../lib/router';
import { useApi } from '../../../lib/useApi';
import { useDashboard } from '../../../state/dashboard';
import type { WidgetProps } from './widgets';

type Mode = NonNullable<ThermostatStatus['mode']>;
const MODE_LABEL: Record<Mode, string> = { off: 'Off', heat: 'Heat', cool: 'Cool', auto: 'Auto' };

/** HomeKit always speaks Celsius; show the unit the thermostat itself shows. */
function shown(celsius: number | null | undefined, units: ThermostatStatus['units']) {
  if (celsius == null) return '—';
  const value = units === 'fahrenheit' ? celsius * 1.8 + 32 : celsius;
  return `${Math.round(value)}°`;
}

/** One step on the thermostat's own scale: 1 °F or half a degree Celsius. */
const stepOf = (data: ThermostatStatus) => (data.units === 'fahrenheit' ? 1 / 1.8 : Math.max(data.step ?? 0.5, 0.5));

function Setpoint({
  label,
  value,
  data,
  disabled,
  readOnly,
  onChange,
}: {
  label: string;
  value: number | null | undefined;
  data: ThermostatStatus;
  disabled: boolean;
  /** Members see the set point; only administrators get the buttons. */
  readOnly: boolean;
  onChange: (value: number) => void;
}) {
  if (value == null) return null;
  const step = stepOf(data);
  if (readOnly)
    return (
      <div className="setpoint">
        <span className="setpoint-label">{label}</span>
        <strong>{shown(value, data.units)}</strong>
      </div>
    );
  return (
    <div className="setpoint">
      <span className="setpoint-label">{label}</span>
      <button
        type="button"
        aria-label={`${label} lower`}
        disabled={disabled || value - step < (data.minimum ?? 10)}
        onClick={() => onChange(value - step)}
      >
        <Minus />
      </button>
      <strong>{shown(value, data.units)}</strong>
      <button
        type="button"
        aria-label={`${label} higher`}
        disabled={disabled || value + step > (data.maximum ?? 33)}
        onClick={() => onChange(value + step)}
      >
        <Plus />
      </button>
    </div>
  );
}

/** The household thermostat: everyone sees it; administrators change the mode and temperature. */
export function ThermostatWidget({ widget }: WidgetProps) {
  const { data: dashboard } = useDashboard();
  const isAdmin = dashboard.identity.is_admin;
  const { data, error, reload } = useApi<ThermostatStatus>('/api/v1/home/thermostat', { interval: 60_000 });
  const [pending, setPending] = useState(false);
  const [optimistic, setOptimistic] = useState<Partial<ThermostatStatus> | null>(null);

  if (!data) return error ? <p className="widget-error">{error}</p> : <p className="muted">Loading…</p>;
  if (!data.paired)
    return (
      <div className="widget-empty">
        <p>No thermostat is paired yet.</p>
        {isAdmin ? (
          <Link to="/settings/home-devices" className="widget-link">
            Pair it in Settings
          </Link>
        ) : (
          <p className="muted">An administrator pairs it in Settings.</p>
        )}
      </div>
    );

  const view = { ...data, ...optimistic };
  const change = async (body: Partial<Pick<ThermostatStatus, 'mode' | 'target' | 'heat_to' | 'cool_to'>>) => {
    setOptimistic((current) => ({ ...current, ...body }));
    setPending(true);
    try {
      await postJsonApi<ThermostatStatus>('/api/v1/home/thermostat', body);
      await reload();
    } catch (cause) {
      toast.error(`The thermostat was not changed: ${errorText(cause)}`);
    } finally {
      setOptimistic(null);
      setPending(false);
    }
  };
  const StateIcon = view.state === 'heating' ? Flame : view.state === 'cooling' ? Snowflake : null;
  // A one-column, one-row widget has room for the reading and set point only; the mode waits for more room.
  const compact = widget.w === 1 && widget.h === 1;
  const size = `${widget.h >= 2 ? ' is-tall' : ' is-short'}${widget.w === 1 ? ' is-narrow' : ''}`;
  return (
    <div className={`thermostat${size}`}>
      <div className="thermostat-now">
        <p className="thermostat-temp">{shown(view.current, view.units)}</p>
        <p className="thermostat-state">
          {StateIcon && <StateIcon aria-hidden />}
          {view.state === 'heating' ? 'Heating' : view.state === 'cooling' ? 'Cooling' : MODE_LABEL[view.mode ?? 'off']}
          {view.humidity != null && (
            <span className="muted">
              <Droplets aria-hidden />
              {Math.round(view.humidity)}%
            </span>
          )}
        </p>
      </div>
      {view.mode === 'auto' ? (
        <div className="setpoints">
          <Setpoint
            label="Heat to"
            value={view.heat_to}
            data={view}
            disabled={pending}
            readOnly={!isAdmin}
            onChange={(value) => void change({ heat_to: value })}
          />
          <Setpoint
            label="Cool to"
            value={view.cool_to}
            data={view}
            disabled={pending}
            readOnly={!isAdmin}
            onChange={(value) => void change({ cool_to: value })}
          />
        </div>
      ) : view.mode !== 'off' ? (
        <div className="setpoints">
          <Setpoint
            label={view.mode === 'cool' ? 'Cool to' : 'Heat to'}
            value={view.target}
            data={view}
            disabled={pending}
            readOnly={!isAdmin}
            onChange={(value) => void change({ target: value })}
          />
        </div>
      ) : null}
      {isAdmin && !compact && (view.modes?.length ?? 0) > 1 && (
        <div className="segmented thermostat-modes" role="radiogroup" aria-label="Thermostat mode">
          {(view.modes ?? []).map((mode) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={view.mode === mode}
              disabled={pending}
              onClick={() => void change({ mode })}
            >
              {MODE_LABEL[mode]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
