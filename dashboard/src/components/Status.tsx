import type { ReactNode } from 'react';
import { stateLabel, stateTone, statusText, type Tone } from '../lib/services';
import type { Service } from '../api';

export function Dot({ tone }: { tone: Tone }) {
  return <span className={`dot dot-${tone}`} aria-hidden="true" />;
}

export function Badge({ tone = 'gray', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

/** An app's status; given the app itself, a busy one says what it is doing ("Stopping…"). */
export function StatusBadge({ state, service }: { state: Service['display_state']; service?: Service }) {
  const tone = service?.backing_up ? 'blue' : stateTone(state);
  return (
    <Badge tone={tone}>
      <Dot tone={tone} />
      {service ? statusText(service) : stateLabel[state] || state}
    </Badge>
  );
}

/** Badge for job, provider, and integration states that are plain strings. */
export function StateBadge({ state, tone }: { state: string; tone?: Tone }) {
  const resolved =
    tone ||
    (['succeeded', 'verified', 'live', 'ready', 'connected'].includes(state)
      ? 'green'
      : ['failed', 'degraded', 'incompatible', 'unsupported_legacy', 'authentication_expired'].includes(state)
        ? 'red'
        : ['queued', 'running', 'verifying', 'starting', 'resetting', 'removing'].includes(state)
          ? 'blue'
          : 'gray');
  return (
    <Badge tone={resolved}>
      <Dot tone={resolved} />
      {state.replaceAll('_', ' ')}
    </Badge>
  );
}
