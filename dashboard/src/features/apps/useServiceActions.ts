import { Download, Play, RefreshCw, RotateCcw, Square, Trash2, type LucideIcon } from 'lucide-react';
import { useState } from 'react';
import { postJsonApi, type Service } from '../../api';
import { useConfirm } from '../../components/Dialog';
import { useAction } from '../../lib/useAction';
import { useDashboard } from '../../state/dashboard';
import { useLifecycle } from './lifecycle';

export type ServiceAction = NonNullable<Service['allowed_actions']>[number];

export const ACTIONS: Record<
  ServiceAction,
  { label: string; icon: LucideIcon; confirm?: string; danger?: boolean; doing?: string }
> = {
  install: {
    label: 'Install',
    doing: 'Installing',
    icon: Download,
    confirm:
      'Mu3Lab downloads the configured images and sets up the app and its private route. Some apps require you to complete account setup.',
  },
  retry_setup: {
    label: 'Retry setup',
    doing: 'Setting up',
    icon: RotateCcw,
    confirm: 'Setup runs again. Existing data is kept.',
  },
  start: { label: 'Start', doing: 'Starting', icon: Play },
  // Start, stop and restart ask in useLifecycle's dialog (lifecycle.tsx).
  stop: { label: 'Stop', doing: 'Stopping', icon: Square, danger: true },
  restart: { label: 'Restart', doing: 'Restarting', icon: RefreshCw },
  // Both open UninstallDialog, which asks about data; `perform` never runs them.
  uninstall: { label: 'Uninstall…', icon: Trash2, danger: true },
  uninstall_delete_data: { label: 'Uninstall…', icon: Trash2, danger: true },
};

/**
 * Runs allowlisted lifecycle actions as background jobs, confirming disruptive ones first.
 * Start, stop and restart go through `useLifecycle`, which also looks after the apps involved.
 */
export function useServiceActions(service: Service) {
  const confirm = useConfirm();
  const lifecycle = useLifecycle();
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const [deciding, setDeciding] = useState('');
  const perform = async (action: ServiceAction) => {
    if (action === 'start' || action === 'stop' || action === 'restart') {
      setDeciding(action);
      try {
        await lifecycle(action, [service]);
      } finally {
        setDeciding('');
      }
      return;
    }
    const meta = ACTIONS[action];
    if (
      meta.confirm &&
      !(await confirm({
        title: `${meta.label} ${service.name}?`,
        description: meta.confirm,
        confirmLabel: meta.label,
        tone: meta.danger ? 'danger' : 'primary',
      }))
    )
      return;
    await run(
      action,
      () => postJsonApi(`/api/v1/services/${service.id}/actions`, { action }),
      meta.doing ? `${meta.doing} ${service.name}…` : `${meta.label} started for ${service.name}`,
    );
    void refresh();
  };
  return { pending: pending || deciding, perform, actions: service.allowed_actions || [] };
}
