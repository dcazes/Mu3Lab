import { CheckCircle2, ChevronDown, ChevronUp, GripVertical, Pause, Play, RotateCcw, X } from 'lucide-react';
import { useState } from 'react';
import type { ImageDownload, InstallBatch, InstallBatchItem, InstallBatchJob, Service } from '../../api';
import { Button } from '../../components/Button';
import { Collapsible } from '../../components/Layout';
import { StateBadge } from '../../components/Status';
import { bytes, humanize } from '../../lib/format';

const ACTIVE = ['queued', 'running', 'resetting'];

function timeLeft(seconds: number) {
  if (seconds < 60) return 'less than a minute left';
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `about ${minutes} min left` : `about ${Math.floor(minutes / 60)} h ${minutes % 60} min left`;
}

/** One line of download progress: amount, speed and time left, or what Docker is doing. */
export function DownloadStatus({ download }: { download: ImageDownload }) {
  if (download.state === 'docker') return <p className="download-status">Downloading with Docker…</p>;
  if (download.state === 'loading')
    return (
      <p className="download-status">
        Unpacking into Docker ({download.images_done} of {download.images_total})…
      </p>
    );
  const percent = download.total_bytes ? Math.min(100, (download.done_bytes / download.total_bytes) * 100) : 0;
  const left = download.rate_bps > 0 ? (download.total_bytes - download.done_bytes) / download.rate_bps : 0;
  return (
    <div className="download-status">
      <div
        className="progress"
        role="progressbar"
        aria-label="Download progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
      <p>
        {Math.floor(percent)}% · {bytes(download.done_bytes)} of {bytes(download.total_bytes)}
        {download.rate_bps > 0 && ` · ${bytes(download.rate_bps)}/s · ${timeLeft(left)}`}
      </p>
    </div>
  );
}

export interface InstallControls {
  pause: (serviceId: string) => void;
  resume: (serviceId: string) => void;
  reorder: (serviceIds: string[]) => void;
  setParallel: (count: number) => void;
}

const MAX_PARALLEL = 8;

/** What an app is doing, before and during setup. */
function itemStatus(item: InstallBatchItem, job: InstallBatchJob | null, isCurrent: boolean) {
  if (item.state === 'pending') {
    if (item.download_state === 'downloading') return { label: 'downloading', tone: 'blue' as const };
    if (item.download_state === 'paused') return { label: 'paused', tone: 'gray' as const };
    if (item.download_state === 'ready') return { label: 'downloaded', tone: 'gray' as const };
    return { label: 'waiting', tone: 'gray' as const };
  }
  if (item.state === 'queued' && !(isCurrent && job?.state === 'running'))
    return { label: 'waiting to set up', tone: 'blue' as const };
  if (['queued', 'running'].includes(item.state)) return { label: 'setting up', tone: 'blue' as const };
  if (item.state === 'succeeded') return { label: 'ready', tone: undefined };
  return { label: item.state, tone: undefined };
}

function ItemDetail({
  item,
  job,
  isCurrent,
}: {
  item: InstallBatchItem;
  job: InstallBatchJob | null;
  isCurrent: boolean;
}) {
  const download = item.download;
  if (item.state === 'pending') {
    if (item.download_state === 'downloading')
      return download && download.state !== 'done' ? (
        <DownloadStatus download={download} />
      ) : (
        <p className="download-status">Starting download…</p>
      );
    if (item.download_state === 'paused')
      return (
        <p className="download-status">
          Paused
          {download?.total_bytes
            ? ` at ${Math.floor((download.done_bytes / download.total_bytes) * 100)}% · ${bytes(download.done_bytes)} of ${bytes(download.total_bytes)}`
            : ''}
        </p>
      );
    if (item.download_state === 'ready')
      return <p className="download-status">Downloaded · waiting for its turn to set up</p>;
    return <p className="download-status">Waiting to download</p>;
  }
  if (['queued', 'running'].includes(item.state) && isCurrent && job?.state === 'running') {
    if (job.step_id === 'pull_images' && download && download.state !== 'done')
      return <DownloadStatus download={download} />;
    return <p className="download-status">{humanize(job.step_id || 'starting')}</p>;
  }
  return null;
}

export function InstallProgress({
  batch,
  job,
  services,
  onAction,
  onDismiss,
  controls,
}: {
  batch: InstallBatch;
  job: InstallBatchJob | null;
  services: Service[];
  onAction: (action: 'cancel' | 'reset') => void;
  onDismiss: () => void;
  controls?: InstallControls;
}) {
  const [dragging, setDragging] = useState('');
  const name = (id: string) => services.find((service) => service.id === id)?.name || id;
  const total = batch.items.length;
  const done = batch.items.filter((item) => item.state === 'succeeded').length;
  const active =
    batch.items.find((item) => item.ordinal === batch.current_ordinal) ||
    batch.items.find((item) => ACTIVE.includes(item.state));
  const resettable = ['paused', 'cancelled', 'completed_with_failures', 'reset_failed'].includes(batch.state);
  const log = (job?.events || []).map((event) => `${new Date(event.created_at).toLocaleTimeString()}  ${event.detail}`);
  const running = ['queued', 'running'].includes(batch.state);
  const movable = (item: InstallBatchItem) => running && !!controls && item.state === 'pending';
  const order = batch.items.map((item) => item.service_id);
  const move = (serviceId: string, target: number) => {
    const next = order.filter((id) => id !== serviceId);
    next.splice(Math.max(0, Math.min(target, next.length)), 0, serviceId);
    controls?.reorder(next);
  };

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
          <p>
            {active && ['queued', 'running'].includes(active.state)
              ? `Setting up ${name(active.service_id)} · ${humanize(job?.step_id || job?.state || active.state)}`
              : running
                ? 'Downloading. Each app is set up as soon as its download finishes.'
                : ''}
          </p>
        </div>
        <div className="install-header-side">
          {running && controls && (
            <label className="install-parallel">
              <span>Downloads at once</span>
              <select
                value={batch.parallel_downloads || 3}
                onChange={(event) => controls.setParallel(Number(event.target.value))}
              >
                {Array.from({ length: MAX_PARALLEL }, (_, index) => index + 1).map((count) => (
                  <option key={count} value={count}>
                    {count}
                  </option>
                ))}
              </select>
            </label>
          )}
          <span className="install-count">
            {done}/{total}
          </span>
        </div>
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
        {batch.items.map((item, index) => {
          const isCurrent = item.ordinal === active?.ordinal;
          const status = itemStatus(item, job, isCurrent);
          const canMove = movable(item);
          return (
            <li
              key={`${item.service_id}-${item.ordinal}`}
              className={`${canMove ? 'is-movable' : ''} ${dragging === item.service_id ? 'is-dragging' : ''}`}
              draggable={canMove}
              onDragStart={(event) => {
                setDragging(item.service_id);
                event.dataTransfer.effectAllowed = 'move';
              }}
              onDragEnd={() => setDragging('')}
              onDragOver={(event) => {
                if (dragging && canMove) event.preventDefault();
              }}
              onDrop={(event) => {
                event.preventDefault();
                if (dragging && dragging !== item.service_id) move(dragging, index);
                setDragging('');
              }}
            >
              <div className="install-item-row">
                <span className="install-item-name">
                  {canMove && <GripVertical className="install-grip" aria-hidden />}
                  {name(item.service_id)}
                  {!item.explicitly_selected && <small> · required dependency</small>}
                </span>
                <span className="install-item-actions">
                  {canMove && (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={ChevronUp}
                        aria-label={`Move ${name(item.service_id)} up`}
                        disabled={index === 0 || !movable(batch.items[index - 1])}
                        onClick={() => move(item.service_id, index - 1)}
                      />
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={ChevronDown}
                        aria-label={`Move ${name(item.service_id)} down`}
                        disabled={index === batch.items.length - 1}
                        onClick={() => move(item.service_id, index + 1)}
                      />
                    </>
                  )}
                  {running &&
                    controls &&
                    item.state === 'pending' &&
                    ['', 'downloading'].includes(item.download_state) && (
                      <Button size="sm" variant="ghost" icon={Pause} onClick={() => controls.pause(item.service_id)}>
                        Pause
                      </Button>
                    )}
                  {running && controls && item.state === 'pending' && item.download_state === 'paused' && (
                    <Button size="sm" variant="ghost" icon={Play} onClick={() => controls.resume(item.service_id)}>
                      Resume
                    </Button>
                  )}
                  <StateBadge state={status.label} tone={status.tone} />
                </span>
              </div>
              <ItemDetail item={item} job={job} isCurrent={isCurrent} />
            </li>
          );
        })}
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
