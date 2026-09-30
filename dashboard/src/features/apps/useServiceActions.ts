import { Download, Play, RefreshCw, RotateCcw, Square, Trash2, Wrench, type LucideIcon } from 'lucide-react';
import { postJsonApi, type Service } from '../../api';
import { useConfirm } from '../../components/Dialog';
import { useAction } from '../../lib/useAction';
import { useDashboard } from '../../state/dashboard';

export type ServiceAction = NonNullable<Service['allowed_actions']>[number];

export const ACTIONS: Record<ServiceAction, { label: string; icon: LucideIcon; confirm?: string; danger?: boolean }> = {
  install: {
    label: 'Install',
    icon: Download,
    confirm:
      'Mu3Lab downloads the configured images and sets up the app and its private route. Some apps require you to complete account setup.',
  },
  retry_setup: { label: 'Retry setup', icon: RotateCcw, confirm: 'Setup runs again. Existing data is kept.' },
  start: { label: 'Start', icon: Play },
  stop: { label: 'Stop', icon: Square, confirm: 'The app becomes unavailable until you start it again.', danger: true },
  restart: { label: 'Restart', icon: RefreshCw, confirm: 'The app is briefly unavailable while it restarts.' },
  repair: {
    label: 'Repair',
    icon: Wrench,
    confirm: 'Containers are recreated with the current configuration. Data is kept and nothing is downloaded.',
  },
  // Both open UninstallDialog, which asks about data; `perform` never runs them.
  uninstall: { label: 'Uninstall…', icon: Trash2, danger: true },
  uninstall_delete_data: { label: 'Uninstall…', icon: Trash2, danger: true },
};

/** Runs allowlisted lifecycle actions as background jobs, confirming disruptive ones first. */
export function useServiceActions(service: Service) {
  const confirm = useConfirm();
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const perform = async (action: ServiceAction) => {
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
      `${meta.label} started for ${service.name}`,
    );
    void refresh();
  };
  return { pending, perform, actions: service.allowed_actions || [] };
}
