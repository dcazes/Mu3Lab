import { ArrowRight, Loader2 } from 'lucide-react';
import type { Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button, ExternalButton, LinkButton } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Callout, Facts } from '../../components/Layout';
import { Badge, StatusBadge } from '../../components/Status';
import { bytes } from '../../lib/format';
import { isRunning, isWorking, launchTarget, signInSummary } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { ACTIONS, type ServiceAction, useServiceActions } from '../apps/useServiceActions';

const CONTROLS: ServiceAction[] = ['start', 'stop', 'restart'];

/** Status and everyday controls for one app, without leaving Home. */
export function AppQuickPanel({ service, onClose }: { service: Service; onClose: () => void }) {
  const { data } = useDashboard();
  const { pending, perform, actions } = useServiceActions(service);
  const target = launchTarget(service);
  const summary = data.catalog.services[service.id]?.tagline || '';
  const busy = isWorking(service) || Boolean(service.backing_up);
  // While a start or stop runs, its buttons go; the status shows what is happening instead.
  const controls = busy ? [] : CONTROLS.filter((action) => actions.includes(action));
  const mode = service.identity?.mode;
  // Single sign-on is the usual case; only a login of its own is worth a line.
  const ownLogin = mode === 'local' || mode === 'proxy_gate';
  const memory = data.system.memory_by_owner?.[service.id] || 0;
  const disk = data.system.disk_usage?.owners?.[service.id];
  const diskBytes = (disk?.data || 0) + (disk?.program || 0);
  // What to do next matters only when the app needs something; "stopped" says enough on its own.
  const problem =
    service.display_state === 'needs_attention' || service.display_state === 'needs_setup'
      ? service.reason || service.detail
      : '';
  const facts = [
    ...(isRunning(service) ? [{ label: 'Memory', value: memory ? bytes(memory) : '—' }] : []),
    ...(diskBytes ? [{ label: 'Disk', value: bytes(diskBytes) }] : []),
    ...(ownLogin ? [{ label: 'Sign-in', value: signInSummary(service).label }] : []),
  ];
  return (
    <Dialog
      open
      onClose={onClose}
      size="sheet"
      icon={<AppIcon id={service.id} size="lg" />}
      title={service.name}
      description={summary || undefined}
      footer={
        <LinkButton variant="ghost" size="sm" to={`/apps/${service.id}`} className="quick-panel-details">
          All details
          <ArrowRight />
        </LinkButton>
      }
    >
      <div className="quick-panel-status">
        <StatusBadge state={service.display_state} service={service} />
        {service.update?.update_available && <Badge tone="blue">Update ready</Badge>}
      </div>
      {busy && (
        <p className="quick-panel-busy" aria-live="polite">
          <Loader2 className="spin" />
          You can close this; {service.name} carries on, and Home shows when it is done.
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
        <div className="quick-panel-actions">
          {target && (
            <ExternalButton variant="primary" href={target.url} className="quick-panel-open">
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
      {facts.length > 0 && <Facts items={facts} />}
    </Dialog>
  );
}
