import { ArrowUpCircle } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { postJsonApi, type Service } from '../../api';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { errorText } from '../../lib/format';
import { useDashboard, useIsAdmin } from '../../state/dashboard';
import { activeJob } from './JobProgress';

/** An app an administrator can update from here now: a new approved release, nothing else under way. */
export function canUpdate(service: Service) {
  return (
    service.installed &&
    service.stage !== 'foundation' &&
    Boolean(service.update?.update_available) &&
    !activeJob(service) &&
    !service.backing_up
  );
}

/** "10.10.7 → 10.11.0", or what changes when the version stays the same. */
function change(service: Service) {
  const update = service.update!;
  if (update.files_only) return 'Setup changes';
  if (update.supporting_only) return 'Supporting services';
  return `${update.installed_version || 'current'} → ${update.approved_version}`;
}

/**
 * Update every app with an approved update, from the Apps page. Each app's update is its own job,
 * exactly as its own Update button makes: a backup first, and the old version put back by itself if
 * the new one does not start. The worker runs them one after another.
 */
export function UpdateAll() {
  const isAdmin = useIsAdmin();
  const { data, refresh } = useDashboard();
  const confirm = useConfirm();
  const [pending, setPending] = useState(false);
  const updatable = data.services.services.filter(canUpdate);
  if (!isAdmin || !updatable.length) return null;
  const apps = updatable.length === 1 ? '1 app' : `${updatable.length} apps`;

  const run = async () => {
    // Core apps reach further than themselves while they are stopped (LiteLLM answers every chat).
    const warnings = updatable.map((service) => service.stop_warning).filter(Boolean);
    // The same change for every app (as after a Mu3Lab release): said once, above their names.
    const notes = new Set(updatable.map(change));
    const shared = notes.size === 1 && updatable.length > 1 ? [...notes][0] : '';
    const confirmed = await confirm({
      title: `Update ${apps}?`,
      description: (
        <>
          <span className={`update-all-list${updatable.length > 6 ? ' is-columns' : ''}`}>
            {shared && (
              <small className="update-all-shared">
                {shared === 'Setup changes' ? 'Mu3Lab’s setup changes; each app keeps its version' : shared}
              </small>
            )}
            {updatable.map((service) => (
              <span key={service.id}>
                <b>{service.name}</b>
                {!shared && <small>{change(service)}</small>}
              </span>
            ))}
          </span>
          Mu3Lab updates them one at a time. Each one has its data backed up, then starts on the new version; if it
          doesn’t start properly, Mu3Lab puts back the backup and the version from before. A running app is unavailable
          for a few minutes during its turn; a stopped one is started only to check it, then stopped again.
          {warnings.length > 0 && <span className="update-all-warnings">{warnings.join(' ')}</span>}
        </>
      ),
      confirmLabel: `Update ${apps}`,
    });
    if (!confirmed) return;
    setPending(true);
    const failed: string[] = [];
    try {
      for (const service of updatable) {
        try {
          await postJsonApi(`/api/v1/services/${service.id}/actions`, { action: 'update' });
        } catch (cause) {
          failed.push(`${service.name} (${errorText(cause)})`);
        }
      }
    } finally {
      setPending(false);
    }
    const queued = updatable.length - failed.length;
    if (queued) toast.success(`Updating ${queued} ${queued === 1 ? 'app' : 'apps'}, one at a time.`);
    if (failed.length) toast.error(`Not started: ${failed.join('; ')}`);
    void refresh();
  };

  return (
    <Button size="sm" icon={ArrowUpCircle} loading={pending} onClick={() => void run()}>
      Update all ({updatable.length})
    </Button>
  );
}
