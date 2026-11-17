import { HardDrive } from 'lucide-react';
import { useState } from 'react';
import type { BackupDrive, BackupLocation, JobResponse } from '../../api';
import { postJsonApi } from '../../api/client';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Facts } from '../../components/Layout';
import { bytes } from '../../lib/format';
import { JOB_POLL_MS, jobActive } from '../../lib/jobs';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';

function label(drive: BackupDrive) {
  const model = drive.detail && drive.detail !== drive.name ? ` (${drive.detail})` : '';
  return `${drive.name}${model}: ${bytes(drive.free_bytes)} free`;
}

/**
 * Which drive keeps the backups. Moving them is a job: backups pause while Mu3Lab copies and
 * checks them, and the old copy goes only once the new one has passed Kopia's check.
 */
export function BackupLocationSection() {
  const { data, reload } = useApi<BackupLocation>('/api/v1/system/backups/location', {
    interval: (latest) => (latest?.move && jobActive(latest.move.state) ? JOB_POLL_MS : 0),
  });
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [picked, setPicked] = useState('');
  if (!data) return null;
  const current = data.drives.find((drive) => drive.current);
  const moving = Boolean(data.move && jobActive(data.move.state));
  const failed = data.move?.state === 'failed' ? data.move : null;
  const target = data.drives.find((drive) => drive.id === (picked || current?.id));
  const move = async () => {
    if (!target || target.current) return;
    const ok = await confirm({
      title: `Move the backups to ${target.name}?`,
      description:
        `Mu3Lab pauses backups, copies ${bytes(data.used_bytes)} to ${target.path}, checks every backup there, ` +
        'and only then removes the old copy. Large backups take a while; nothing is lost if it stops halfway.',
      confirmLabel: 'Move backups',
    });
    if (!ok) return;
    const started = await run(
      'move',
      () => postJsonApi<JobResponse>('/api/v1/system/backups/location', { target: target.id }),
      'Moving the backups. Backups pause until the move is done.',
    );
    if (started) {
      setPicked('');
      void reload();
    }
  };
  return (
    <section className="card-section" aria-labelledby="backup-location">
      <h3 id="backup-location">Backup drive</h3>
      <p className="muted">
        Keep backups on a different drive from your apps’ data, so one failing drive can’t take both.
      </p>
      {data.problem && (
        <Callout tone="danger" title="The backup drive is missing">
          {data.problem}
        </Callout>
      )}
      {moving && data.move && (
        <Callout tone="info" title="Moving the backups">
          {data.move.detail} Backups are paused until this finishes.
        </Callout>
      )}
      {failed && !moving && (
        <Callout tone="warning" title="The last move did not finish">
          {failed.detail} The backups stayed where they were.
        </Callout>
      )}
      {!moving && current?.same_drive_as_data && data.drives.some((drive) => !drive.same_drive_as_data) && (
        <Callout tone="warning" title="Backups share a drive with your data">
          If this drive fails, the backups go with it. Choose another drive below.
        </Callout>
      )}
      <Facts
        items={[
          { label: 'Kept in', value: data.path },
          { label: 'Size', value: data.used_bytes ? bytes(data.used_bytes) : '—' },
          { label: 'Free on that drive', value: current ? bytes(current.free_bytes) : '—' },
        ]}
      />
      <div className="form schedule-form">
        <fieldset disabled={moving} className="plain-fieldset">
          <label className="field">
            <span>Drive</span>
            <select value={target?.id ?? ''} onChange={(event) => setPicked(event.target.value)}>
              {data.drives.map((drive) => (
                <option key={drive.id} value={drive.id} disabled={Boolean(drive.problem)}>
                  {label(drive)}
                  {drive.current ? ' (in use)' : ''}
                </option>
              ))}
            </select>
          </label>
          {target && !target.current && target.problem && <p className="warning-text">{target.problem}</p>}
          {target && !target.current && target.desktop_only && (
            <p className="warning-text">
              This drive is connected only while someone is signed in to the desktop, so night-time backups may miss it.
            </p>
          )}
          {target && !target.current && target.same_drive_as_data && (
            <p className="muted">This is the same drive as your apps’ data.</p>
          )}
          <div className="button-row">
            <Button
              icon={HardDrive}
              loading={pending === 'move'}
              disabled={!target || target.current || Boolean(target.problem) || moving}
              onClick={() => void move()}
            >
              Move backups
            </Button>
          </div>
        </fieldset>
      </div>
    </section>
  );
}
