import { Archive } from 'lucide-react';
import { postJsonApi, type BackupProtection, type PlatformBackupStatus } from '../../api';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Card, Facts } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { relativeTime } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

const PLATFORM_JOB = 'mu3lab-platform';

function state(protection: BackupProtection | null | undefined) {
  if (!protection) return <Badge>Not backed up</Badge>;
  const when = protection.completed_at ? ` · ${relativeTime(protection.completed_at)}` : '';
  if (protection.state === 'checked') return <Badge tone="green">{`Checked${when}`}</Badge>;
  if (protection.state === 'missing') return <Badge tone="amber">No backup yet</Badge>;
  return <Badge tone="amber">{`${protection.state === 'stale' ? 'Out of date' : 'Not checked yet'}${when}`}</Badge>;
}

/** Mu3Lab's own state and its core apps: what a rebuilt machine needs besides each app's data. */
export function PlatformBackupCard() {
  const confirm = useConfirm();
  const { data, refresh } = useDashboard();
  const { pending, run } = useAction();
  const { data: status, reload } = useApi<PlatformBackupStatus>('/api/v1/system/backups/platform');
  const busy = data.jobs.jobs.some(
    (job) => job.service_id === PLATFORM_JOB && ['queued', 'running'].includes(job.state),
  );
  const backUp = async () => {
    if (
      !(await confirm({
        title: 'Back up Mu3Lab now?',
        description:
          'Mu3Lab saves its own settings and secrets, then stops sign-in, the password vault and chat for a few minutes while their data is copied. They start again on their own.',
        confirmLabel: 'Back up',
      }))
    )
      return;
    await run('platform', () => postJsonApi('/api/v1/system/backups/platform', {}), 'Backing up Mu3Lab');
    void refresh();
    void reload();
  };
  return (
    <Card
      title="Mu3Lab itself"
      description="Your settings, people, every app's passwords and keys, and the core apps. Without these, restored app data cannot be opened on a new computer."
      actions={
        <Button size="sm" icon={Archive} loading={pending === 'platform'} disabled={busy} onClick={() => void backUp()}>
          Back up now
        </Button>
      }
    >
      {status && (
        <Facts
          items={[
            { label: 'Mu3Lab settings and secrets', value: state(status.platform) },
            ...status.core_apps.map((app) => ({
              label: app.name,
              value: app.included ? state(app.protection) : <span className="muted">Not backed up: {app.reason}</span>,
            })),
          ]}
        />
      )}
    </Card>
  );
}
