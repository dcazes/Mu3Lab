import { CheckCircle2, RotateCcw, X } from 'lucide-react';
import type { InstallBatch, InstallBatchJob, Service } from '../../api';
import { Button } from '../../components/Button';
import { Collapsible } from '../../components/Layout';
import { StateBadge } from '../../components/Status';
import { humanize } from '../../lib/format';

const ACTIVE = ['queued', 'running', 'resetting'];

export function InstallProgress({
  batch,
  job,
  services,
  onAction,
  onDismiss,
}: {
  batch: InstallBatch;
  job: InstallBatchJob | null;
  services: Service[];
  onAction: (action: 'cancel' | 'reset') => void;
  onDismiss: () => void;
}) {
  const name = (id: string) => services.find((service) => service.id === id)?.name || id;
  const total = batch.items.length;
  const done = batch.items.filter((item) => item.state === 'succeeded').length;
  const active =
    batch.items.find((item) => item.ordinal === batch.current_ordinal) ||
    batch.items.find((item) => ACTIVE.includes(item.state));
  const resettable = ['paused', 'cancelled', 'completed_with_failures', 'reset_failed'].includes(batch.state);
  const log = (job?.events || []).map((event) => `${new Date(event.created_at).toLocaleTimeString()}  ${event.detail}`);

  if (batch.state === 'succeeded')
    return (
      <div className="card install-progress install-done">
        <CheckCircle2 className="install-done-icon" />
        <div>
          <b>Installation complete</b>
          <p>
            {done} app{done === 1 ? '' : 's'} installed: {batch.items.map((item) => name(item.service_id)).join(', ')}.
          </p>
        </div>
        <Button variant="ghost" size="sm" icon={X} aria-label="Dismiss" onClick={onDismiss} />
      </div>
    );

  return (
    <section className="card install-progress" aria-live="polite" aria-label="Installation progress">
      <header className="install-header">
        <div>
          <b>{ACTIVE.includes(batch.state) ? 'Installing apps' : humanize(batch.state)}</b>
          <p>{active ? `${name(active.service_id)} · ${humanize(job?.step_id || job?.state || active.state)}` : ''}</p>
        </div>
        <span className="install-count">
          {done}/{total}
        </span>
      </header>
      <div
        className="progress"
        role="progressbar"
        aria-label="Installation progress"
        aria-valuemin={0}
        aria-valuemax={total || 1}
        aria-valuenow={done}
      >
        <span style={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }} />
      </div>
      <ul className="install-items">
        {batch.items.map((item) => (
          <li key={`${item.service_id}-${item.ordinal}`}>
            <span>
              {name(item.service_id)}
              {!item.explicitly_selected && <small> · required dependency</small>}
            </span>
            <StateBadge state={item.ordinal === active?.ordinal && job ? job.state : item.state} />
          </li>
        ))}
      </ul>
      {batch.error?.message && <p className="error-text">{batch.error.message}</p>}
      {log.length > 0 && (
        <Collapsible title="Live activity">
          <pre className="log">{log.slice(-20).join('\n')}</pre>
        </Collapsible>
      )}
      <footer className="install-actions">
        {['queued', 'running'].includes(batch.state) && (
          <Button size="sm" onClick={() => onAction('cancel')}>
            Cancel remaining
          </Button>
        )}
        {resettable && (
          <Button size="sm" variant="primary" icon={RotateCcw} onClick={() => onAction('reset')}>
            Reset failed installation
          </Button>
        )}
      </footer>
    </section>
  );
}
