import { ArrowRight, Loader2, X } from 'lucide-react';
import type { Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button, ExternalButton, LinkButton } from '../../components/Button';
import { Callout } from '../../components/Layout';
import { Badge, StatusBadge } from '../../components/Status';
import { bytes } from '../../lib/format';
import { isRunning, isWorking, launchTarget, needsAttention } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { ACTIONS, type ServiceAction, useServiceActions } from './useServiceActions';

const CONTROLS: ServiceAction[] = ['start', 'stop', 'restart'];

/**
 * The picked app's status and everyday controls, beside the tiles (at the bottom on a phone).
 * Nothing is dimmed: picking another tile swaps what it shows.
 */
export function AppDetailsCard({ service, onClose }: { service: Service; onClose: () => void }) {
  const { data } = useDashboard();
  const { pending, perform, actions } = useServiceActions(service);
  const target = launchTarget(service);
  const summary = data.catalog.services[service.id]?.tagline || '';
  const busy = isWorking(service) || Boolean(service.backing_up);
  // While a start or stop runs, its buttons go; the status shows what is happening instead.
  const controls = busy ? [] : CONTROLS.filter((action) => actions.includes(action));
  const memory = data.system.memory_by_owner?.[service.id] || 0;
  const disk = data.system.disk_usage?.owners?.[service.id];
  const diskBytes = (disk?.data || 0) + (disk?.program || 0);
  // What to do next matters only when the app needs something; "stopped" says enough on its own.
  const problem =
    service.display_state === 'needs_attention' || service.display_state === 'needs_setup'
      ? service.reason || service.detail
      : '';
  return (
    <aside className="details-card" aria-label={`${service.name} details`}>
      <header className="details-card-head">
        <AppIcon id={service.id} size="lg" />
        <div className="details-card-title">
          <h3>{service.name}</h3>
          {summary && <p>{summary}</p>}
        </div>
        <Button variant="ghost" size="sm" icon={X} className="btn-icon" aria-label="Close" onClick={onClose} />
      </header>
      <div className="details-card-status">
        <StatusBadge state={service.display_state} service={service} />
        {service.update?.update_available && <Badge tone="blue">Update ready</Badge>}
      </div>
      {busy && (
        <p className="details-card-busy" aria-live="polite">
          <Loader2 className="spin" />
          {service.name} carries on if you leave; its tile shows when it is done.
        </p>
      )}
      {problem && (
        <Callout
          tone={service.display_state === 'needs_attention' ? 'danger' : 'warning'}
          title={service.display_state === 'needs_attention' ? 'What went wrong' : 'Before you can use it'}
        >
          {problem}
        </Callout>
      )}
      {(target || controls.length > 0) && (
        <div className="details-card-actions">
          {target && (
            <ExternalButton variant="primary" href={target.url} className="details-card-open">
              {target.label}
            </ExternalButton>
          )}
          {controls.map((action) => (
            <Button
              key={action}
              icon={ACTIONS[action].icon}
              variant={!target && action === 'start' ? 'primary' : 'secondary'}
              loading={pending === action}
              disabled={Boolean(pending)}
              onClick={() => void perform(action)}
            >
              {ACTIONS[action].label}
            </Button>
          ))}
        </div>
      )}
      {(isRunning(service) || diskBytes > 0) && (
        <dl className="details-card-stats">
          {isRunning(service) && (
            <div>
              <dt>Memory</dt>
              <dd>{memory ? bytes(memory) : '—'}</dd>
            </div>
          )}
          {diskBytes > 0 && (
            <div>
              <dt>Disk</dt>
              <dd>{bytes(diskBytes)}</dd>
            </div>
          )}
        </dl>
      )}
      <LinkButton variant="ghost" size="sm" to={`/apps/${service.id}`} className="details-card-more">
        All details
        <ArrowRight />
      </LinkButton>
    </aside>
  );
}

/** Before an app is picked: how the installed apps are doing, with anything that needs a look. */
export function AppsSummaryCard({ services, onPick }: { services: Service[]; onPick: (id: string) => void }) {
  const running = services.filter(isRunning).length;
  const updates = services.filter((service) => service.update?.update_available);
  const broken = services.filter(needsAttention);
  const busy = services.filter((service) => isWorking(service) || service.backing_up);
  const flagged = [
    ...broken.map((service) => ({ service, note: 'Needs attention', tone: 'red' as const })),
    ...busy.map((service) => ({ service, note: 'Busy', tone: 'blue' as const })),
    ...updates.map((service) => ({ service, note: 'Update ready', tone: 'blue' as const })),
  ];
  return (
    <aside className="details-card details-summary" aria-label="Apps at a glance">
      <dl className="details-card-stats">
        <div>
          <dt>Running</dt>
          <dd>{running}</dd>
        </div>
        <div>
          <dt>Stopped</dt>
          <dd>{services.length - running}</dd>
        </div>
      </dl>
      {flagged.length ? (
        <ul className="details-summary-list">
          {flagged.map(({ service, note, tone }) => (
            <li key={`${service.id}-${note}`}>
              <button type="button" onClick={() => onPick(service.id)}>
                <AppIcon id={service.id} />
                <span>{service.name}</span>
                <Badge tone={tone}>{note}</Badge>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Nothing needs a look. Pick an app to see its details.</p>
      )}
    </aside>
  );
}
