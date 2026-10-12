import { ArrowRight } from 'lucide-react';
import type { Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button, ExternalButton, LinkButton } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Facts } from '../../components/Layout';
import { Badge, StatusBadge } from '../../components/Status';
import { bytes } from '../../lib/format';
import { launchTarget, signInSummary } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { ACTIONS, type ServiceAction, useServiceActions } from '../apps/useServiceActions';

const CONTROLS: ServiceAction[] = ['start', 'stop', 'restart'];

/** Status and everyday controls for one app, without leaving Home. */
export function AppQuickPanel({ service, onClose }: { service: Service; onClose: () => void }) {
  const { data } = useDashboard();
  const { pending, perform, actions } = useServiceActions(service);
  const target = launchTarget(service);
  const memory = data.system.container_memory || {};
  const containers = service.containers || [];
  const used = containers.reduce((sum, container) => sum + (memory[container.name] || 0), 0);
  const running = containers.filter((container) => container.state === 'running').length;
  const signIn = signInSummary(service);
  const summary = data.catalog.services[service.id]?.tagline || '';
  return (
    <Dialog open onClose={onClose} size="drawer" title={service.name} description={summary || undefined}>
      <div className="quick-panel-head">
        <AppIcon id={service.id} size="lg" />
        <StatusBadge state={service.display_state} />
        {service.update?.update_available && <Badge tone="blue">Update ready</Badge>}
      </div>
      {service.display_state !== 'running' && service.detail && <p className="muted">{service.detail}</p>}
      <div className="button-row">
        {target && (
          <ExternalButton variant="primary" href={target.url}>
            {target.label}
          </ExternalButton>
        )}
        {CONTROLS.filter((action) => actions.includes(action)).map((action) => (
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
      <Facts
        items={[
          { label: 'Memory', value: used ? bytes(used) : '—' },
          {
            label: 'Containers',
            value: containers.length ? `${running} of ${containers.length} running` : '—',
          },
          { label: 'Sign-in', value: signIn.label || '—' },
        ]}
      />
      <LinkButton variant="ghost" to={`/apps/${service.id}`} className="quick-panel-details">
        All details
        <ArrowRight />
      </LinkButton>
    </Dialog>
  );
}
