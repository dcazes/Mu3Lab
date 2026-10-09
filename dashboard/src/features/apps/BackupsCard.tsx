import { Archive, History, RotateCcw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { postJsonApi, type BackupSnapshot, type BackupsResponse, type Service } from '../../api';
import { Button } from '../../components/Button';
import { Dialog, useConfirm } from '../../components/Dialog';
import { Card } from '../../components/Layout';
import { relativeTime } from '../../lib/format';
import { useApi } from '../../lib/useApi';
import { useAction } from '../../lib/useAction';
import { useDashboard } from '../../state/dashboard';

function label(backup: BackupSnapshot) {
  const version = backup.version ? ` (${backup.version})` : '';
  if (backup.reason === 'pre-update') return `Before an update${version}`;
  if (backup.reason === 'pre-restore') return `Before a restore${version}`;
  return `Backup${version}`;
}

function when(backup: BackupSnapshot) {
  return new Date(backup.time).toLocaleString();
}

/** Restoring replaces the app's data, so the owner repeats its name, as for deleting it. */
function RestoreDialog({
  service,
  backup,
  onClose,
}: {
  service: Service;
  backup: BackupSnapshot | null;
  onClose: () => void;
}) {
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const [typed, setTyped] = useState('');
  const close = () => {
    setTyped('');
    onClose();
  };
  const submit = async () => {
    if (!backup) return;
    const result = await run(
      'restore',
      () =>
        postJsonApi(`/api/v1/services/${service.id}/actions`, {
          action: 'restore',
          snapshot_id: backup.id,
          confirm: typed.trim(),
        }),
      `Restoring ${service.name}`,
    );
    if (result === undefined) return;
    close();
    void refresh();
  };
  return (
    <Dialog
      open={Boolean(backup)}
      onClose={close}
      title={`Restore ${service.name}?`}
      description={
        backup
          ? `${service.name}'s data goes back to how it was on ${when(backup)}. Anything changed since then is replaced. ${service.name} stops while this runs.`
          : undefined
      }
      footer={
        <>
          <Button onClick={close}>Cancel</Button>
          <Button
            variant="danger"
            disabled={typed.trim() !== service.name}
            loading={Boolean(pending)}
            onClick={() => void submit()}
          >
            Restore
          </Button>
        </>
      }
    >
      {backup?.version && backup.version !== service.update?.installed_version && (
        <p>
          This backup is from {backup.version}, so {service.name} goes back to {backup.version} as well.
        </p>
      )}
      <p className="muted">Mu3Lab saves the current data first, so you can undo this by restoring that backup.</p>
      <label className="field">
        <span>
          Type <strong>{service.name}</strong> to confirm
        </span>
        <input autoComplete="off" data-autofocus value={typed} onChange={(event) => setTyped(event.target.value)} />
      </label>
    </Dialog>
  );
}

/** An update or restore that could not be undone: only recovery may run until it is settled. */
function Recovery({
  service,
  backups,
  busy,
  onRestore,
}: {
  service: Service;
  backups: BackupSnapshot[] | null;
  busy: boolean;
  onRestore: (backup: BackupSnapshot) => void;
}) {
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const recovery = service.recovery;
  if (!recovery) return null;
  const before = backups?.find((backup) => backup.id === recovery.snapshot_id) ?? null;
  const retry = async () => {
    await run(
      'recover',
      () => postJsonApi(`/api/v1/services/${service.id}/actions`, { action: 'recover' }),
      `Recovering ${service.name}`,
    );
    void refresh();
  };
  return (
    <div className="card-pad recovery" role="alert">
      <p className="warning-text">
        <b>{service.name} needs attention.</b> {recovery.detail}
      </p>
      <p className="muted">
        Other actions are paused until {service.name} is back to a known state. Retrying puts back the data and release
        from before the {recovery.kind}.
      </p>
      <div className="recovery-actions">
        <Button size="sm" icon={RotateCcw} loading={pending === 'recover'} disabled={busy} onClick={() => void retry()}>
          Retry recovery
        </Button>
        {before && (
          <Button size="sm" icon={History} disabled={busy} onClick={() => onRestore(before)}>
            Restore the backup from before…
          </Button>
        )}
      </div>
    </div>
  );
}

export function BackupsCard({ service }: { service: Service }) {
  const confirm = useConfirm();
  const { data, refresh } = useDashboard();
  const { pending, run } = useAction();
  const [restoring, setRestoring] = useState<BackupSnapshot | null>(null);
  const { data: listing, error, reload } = useApi<BackupsResponse>(`/api/v1/services/${service.id}/backups`);
  const backups = listing?.backups ?? null;
  const busy = data.jobs.jobs.some((job) => job.service_id === service.id && ['queued', 'running'].includes(job.state));
  // A backup, restore or update for this app just finished: show what it saved.
  const wasBusy = useRef(busy);
  useEffect(() => {
    if (wasBusy.current && !busy) void reload();
    wasBusy.current = busy;
  }, [busy, reload]);
  const backUp = async () => {
    if (
      !(await confirm({
        title: `Back up ${service.name}?`,
        description: `${service.name} stops for a moment while its data is copied, then starts again.`,
        confirmLabel: 'Back up',
      }))
    )
      return;
    await run(
      'backup',
      () => postJsonApi(`/api/v1/services/${service.id}/actions`, { action: 'backup' }),
      `Backing up ${service.name}`,
    );
    void refresh();
  };
  return (
    <Card
      title="Backups"
      description="Encrypted copies of this app's data, kept on this server. Mu3Lab saves one before every update."
      actions={
        <Button
          size="sm"
          icon={Archive}
          loading={pending === 'backup'}
          disabled={busy || Boolean(service.recovery)}
          onClick={() => void backUp()}
        >
          Back up now
        </Button>
      }
      flush
    >
      <Recovery service={service} backups={backups} busy={busy} onRestore={setRestoring} />
      {listing?.protection && backups && backups.length > 0 && (
        <p className={`card-pad ${listing.protection.state === 'checked' ? 'muted' : 'warning-text'}`}>
          {listing.protection.detail}
        </p>
      )}
      {error ? (
        <p className="muted card-pad">{error}</p>
      ) : !backups ? (
        <p className="muted card-pad">Loading backups…</p>
      ) : backups.length ? (
        <div className="rows">
          {backups.map((backup) => (
            <div className="row" key={backup.id}>
              <span className="row-text">
                <b>{label(backup)}</b>
                <small>{when(backup)}</small>
              </span>
              <small className="row-time">{relativeTime(backup.time)}</small>
              <Button size="sm" icon={History} disabled={busy} onClick={() => setRestoring(backup)}>
                Restore…
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted card-pad">No backups yet.</p>
      )}
      <RestoreDialog service={service} backup={restoring} onClose={() => setRestoring(null)} />
    </Card>
  );
}
