import { AlertTriangle, DatabaseBackup, Loader2, type LucideIcon } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import type { DismissedResponse } from '../../api';
import { postJsonApi } from '../../api/client';
import { isWorking, needsAttention, stateLabel, statusText } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { useCoreSetupActive } from './CoreSetupProgress';

export interface HomeNotice {
  key: string;
  /** What dismissing it records; empty for progress, which clears by itself. */
  warnings: string[];
  icon: LucideIcon;
  tone: 'warning' | 'progress';
  text: string;
  /** Further warnings folded into this row, such as a second backup warning. */
  more: number;
  to: string;
}

/**
 * Warnings the person dismissed, on all their devices. Each warning is named by what it says and
 * when its problem began, so a dismissal lasts only while that same problem continues.
 */
function useDismissed() {
  const { data, refresh } = useDashboard();
  // Hidden at once on a click, before the server has answered.
  const [pending, setPending] = useState<string[]>([]);
  const dismissed = [...(data.dismissed || []), ...pending];
  const dismiss = (warnings: string[]) => {
    setPending((current) => [...current, ...warnings]);
    Promise.all(warnings.map((warning) => postJsonApi<DismissedResponse>('/api/v1/dismissed', { warning })))
      .then(() => refresh())
      .catch(() => {
        setPending((current) => current.filter((item) => !warnings.includes(item)));
        toast.error('That warning could not be hidden. Try again.');
      });
  };
  return [dismissed, dismiss] as const;
}

/** Home's warnings and progress rows; the sidebar's status counts the same warnings. */
export function useHomeNotices() {
  const { data } = useDashboard();
  const [dismissed, dismiss] = useDismissed();
  const failing = data.services.services.filter(needsAttention);
  // The core apps' own progress card covers them while their setup runs.
  const coreActive = useCoreSetupActive();
  const coreApps = new Set(coreActive ? (data.core.apps || []).map((app) => app.service_id) : []);
  const working = data.services.services.filter((service) => isWorking(service) && !coreApps.has(service.id));
  const setupIncomplete = data.provisioning.available && !data.provisioning.complete;
  const setupFailed = setupIncomplete && (data.provisioning.blocked || data.core.current_job?.state === 'failed');
  const worker = data.system.worker_state || '';
  const workerDown = data.system.ok && !['', 'active', 'activating', 'unknown'].includes(worker);
  const warning = (key: string, id: string, text: string, to: string, icon: LucideIcon = AlertTriangle) => ({
    key,
    warnings: [id],
    icon,
    tone: 'warning' as const,
    text,
    more: 0,
    to,
  });
  const notices: HomeNotice[] = [
    // Nothing runs without these two, so they lead.
    ...(data.system.ok && !data.system.docker_ready
      ? [
          warning(
            'docker',
            'docker|down',
            'Docker isn’t reachable, so apps can’t start or run. Open System settings for details.',
            '/settings/system',
          ),
        ]
      : []),
    ...(workerDown
      ? [
          warning(
            'worker',
            `worker|${worker}`,
            `Mu3Lab’s background worker is ${worker}, so installs, backups and repairs wait until it runs again.`,
            '/settings/system',
          ),
        ]
      : []),
    // The button that tries again sits in System settings.
    ...(setupFailed
      ? [
          warning(
            'setup-failed',
            `setup-failed|${data.core.current_job?.id || ''}`,
            'A setup step didn’t finish. Open System settings to try again.',
            '/settings/system',
          ),
        ]
      : []),
    ...failing.map((service) =>
      warning(
        service.id,
        // An app that recovered in between has a newer last success, so a new failure shows again.
        `app|${service.id}|${service.display_state}|${service.reason}|${service.checks?.process?.last_success_at || ''}`,
        service.reason
          ? `${service.name} needs attention: ${service.reason}`
          : `${service.name}: ${stateLabel[service.display_state].toLowerCase()}`,
        `/apps/${service.id}`,
      ),
    ),
  ].filter((item) => !dismissed.includes(item.warnings[0]));
  // Sent to administrators only, and only while backups fall behind the schedule. They share one
  // page, so they share one row: the first (most basic) problem leads and the rest are counted.
  const backups = (setupIncomplete ? [] : data.backup_attention || [])
    .map((item) => ({ ...item, warning: `backup|${item.key}|${item.message}` }))
    .filter((item) => !dismissed.includes(item.warning));
  if (backups.length)
    notices.push({
      key: 'backups',
      warnings: backups.map((item) => item.warning),
      icon: DatabaseBackup,
      tone: 'warning',
      text: backups[0].message,
      more: backups.length - 1,
      to: '/settings/security',
    });
  const progress: HomeNotice[] = working.map((service) => ({
    key: `working-${service.id}`,
    warnings: [],
    icon: Loader2,
    tone: 'progress',
    text: `${service.name} is ${statusText(service).toLowerCase()}`,
    more: 0,
    to: `/apps/${service.id}`,
  }));
  const count = notices.reduce((total, item) => total + 1 + item.more, 0);
  return { notices, progress, count, dismiss };
}
