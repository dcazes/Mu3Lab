import { Archive, Check, History, RotateCcw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { postJsonApi, type BackupSnapshot, type BackupStarted, type BackupsResponse, type Service } from '../../api';
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
  if (backup.reason === 'pre-restore') return `Before a restore${version}`; // made by older versions
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
      <p className="muted">
        If {service.name} does not start on the restored data, Mu3Lab puts the current data back as it was.
      </p>
      <label className="field">
        <span>
          Type <strong>{service.name}</strong> to confirm
        </span>
        <input autoComplete="off" data-autofocus value={typed} onChange={(event) => setTyped(event.target.value)} />
      </label>
    </Dialog>
  );
}

/** A restore or update that was interrupted: nothing else runs until the owner chooses. */
function Interrupted({ service, busy }: { service: Service; busy: boolean }) {
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const recovery = service.recovery;
  if (!recovery) return null;
  const choose = async (action: 'recover' | 'keep_update', label: string) => {
    await run(action, () => postJsonApi(`/api/v1/services/${service.id}/actions`, { action }), label);
    void refresh();
  };
  const update = recovery.kind === 'update';
  return (
    <div className="card-pad recovery" role="alert">
      <p className="warning-text">
        <b>{service.name} needs attention.</b> {recovery.detail}
      </p>
      <p className="muted">
        Nothing else runs on {service.name} until you choose.{' '}
        {update
          ? 'Undoing puts back the release, settings and data from just before the update.'
          : 'Putting the original data back returns it exactly as it was before the restore started.'}
      </p>
      <div className="recovery-actions">
        <Button
          size="sm"
          icon={RotateCcw}
          loading={pending === 'recover'}
          disabled={busy}
          onClick={() =>
            void choose('recover', update ? `Undoing the ${service.name} update` : 'Putting the data back')
          }
        >
          {update ? 'Undo update' : 'Put my original data back'}
        </Button>
        {recovery.can_keep && (
          <Button
            size="sm"
            icon={Check}
            loading={pending === 'keep_update'}
            disabled={busy}
            onClick={() => void choose('keep_update', `Keeping the new ${service.name} release`)}
          >
            Keep new version
          </Button>
        )}
      </div>
    </div>
  );
}

/** The last restore did not work and its data was put back: say so, and offer it again. */
function FailedRestore({ service, busy }: { service: Service; busy: boolean }) {
  const { data, refresh } = useDashboard();
  const { pending, run } = useAction();
  const last = data.jobs.jobs.find((job) => job.service_id === service.id && job.action === 'restore');
  if (!last || last.state !== 'failed' || service.recovery) return null;
  const putBack = last.error_code === 'restore_rolled_back';
  const retry = async () => {
    await run('retry', () => postJsonApi(`/api/v1/jobs/${last.id}/retry`, {}), `Restoring ${service.name} again`);
    void refresh();
  };
  return (
    <div className="card-pad recovery" role="alert">
      <p className="warning-text">
        <b>
          {putBack
            ? `The restore did not work, so ${service.name}'s data was put back as it was.`
            : 'The restore did not happen; nothing changed.'}
        </b>{' '}
        {last.detail}
      </p>
      <div className="recovery-actions">
        <Button size="sm" icon={RotateCcw} loading={pending === 'retry'} disabled={busy} onClick={() => void retry()}>
          Retry
        </Button>
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
  const busy =
    Boolean(service.backing_up) ||
    data.jobs.jobs.some((job) => job.service_id === service.id && ['queued', 'running'].includes(job.state));
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
      () => postJsonApi<BackupStarted>(`/api/v1/services/${service.id}/backups`, {}),
      (result) => result.detail,
    );
    void refresh();
  };
  return (
    <Card
      title="Backups"
      description="Encrypted copies of this app's data, kept on this server by Kopia on your backup schedule. Mu3Lab also saves one before every update."
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
      <Interrupted service={service} busy={busy} />
      <FailedRestore service={service} busy={busy} />
      {service.backing_up && <p className="muted card-pad">Backing up now; {service.name} starts again on its own.</p>}
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
              <Button
                size="sm"
                icon={History}
                disabled={busy || Boolean(service.recovery)}
                onClick={() => setRestoring(backup)}
              >
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
