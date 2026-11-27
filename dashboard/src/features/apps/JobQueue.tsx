import { ArrowRight, ChevronDown, Loader2 } from 'lucide-react';
import { useState } from 'react';
import type { Job, Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Link } from '../../lib/router';
import { isWorking, statusText } from '../../lib/services';
import { activeJob, JobProgress } from './JobProgress';

/** What a waiting app is waiting to do, from its job's action. */
const NEXT: Record<string, string> = {
  install: 'install',
  retry_setup: 'finish setup',
  start: 'start',
  stop: 'stop',
  restart: 'restart',
  update: 'update',
  restore: 'restore',
  uninstall: 'uninstall',
  uninstall_delete_data: 'uninstall',
};

export interface QueueEntry {
  service: Service;
  job: Job | null;
}

const queuedJob = (service: Service) => {
  const job = service.last_job;
  return job && job.state === 'queued' ? job : null;
};

/**
 * The apps Mu3Lab is working on now, and those waiting their turn in the order the worker takes
 * them: oldest first, one at a time, except app updates that may run side by side (the API numbers
 * each waiting job; older servers don't, so the time it was asked for stands in).
 */
export function jobQueue(services: Service[]) {
  const now: QueueEntry[] = [];
  const waiting: QueueEntry[] = [];
  for (const service of services) {
    const queued = queuedJob(service);
    if (queued) waiting.push({ service, job: queued });
    else if (isWorking(service) || service.backing_up)
      now.push({ service, job: service.last_job?.state === 'running' ? service.last_job : null });
  }
  const turn = (entry: QueueEntry) => entry.job?.queue_position ?? Number.MAX_SAFE_INTEGER;
  waiting.sort((a, b) => turn(a) - turn(b) || (a.job?.created_at || '').localeCompare(b.job?.created_at || ''));
  return { now, waiting };
}

/** "Updating Immich" from "Updating…". */
const doing = (service: Service) => `${statusText(service).replace(/…$/, '')} ${service.name}`;

const names = (entries: QueueEntry[]) =>
  new Intl.ListFormat('en', { style: 'long', type: 'conjunction' }).format(
    entries.map((entry) => doing(entry.service)),
  );

/** Every app in the queue, in turn: what runs now, then each waiting app numbered. */
function QueueList({ now, waiting }: { now: QueueEntry[]; waiting: QueueEntry[] }) {
  return (
    <ol className="job-queue-list" aria-label="Order Mu3Lab works through them">
      {now.map(({ service }) => (
        <li key={service.id}>
          <Link to={`/apps/${service.id}`}>
            <span className="job-queue-turn job-queue-now">Now</span>
            <AppIcon id={service.id} size="sm" />
            <b>{service.name}</b>
            <small>{statusText(service)}</small>
          </Link>
        </li>
      ))}
      {waiting.map(({ service, job }, index) => (
        <li key={service.id}>
          <Link to={`/apps/${service.id}`}>
            <span className="job-queue-turn">{index + 1}</span>
            <AppIcon id={service.id} size="sm" />
            <b>{service.name}</b>
            <small>Waiting to {NEXT[job?.action || ''] || 'run'}</small>
          </Link>
        </li>
      ))}
    </ol>
  );
}

/** "3 waiting", with a button that opens the full order. */
function QueueToggle({ count, open, onToggle }: { count: number; open: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="btn btn-ghost btn-sm job-queue-toggle" aria-expanded={open} onClick={onToggle}>
      {count} waiting
      <ChevronDown aria-hidden="true" />
    </button>
  );
}

/**
 * One line for everything Mu3Lab is starting, stopping, updating or installing: the app it is on
 * now and how many wait, opening to the whole order. A long batch stays one row instead of a list.
 */
export function JobQueueNotice({ services }: { services: Service[] }) {
  const [open, setOpen] = useState(false);
  const { now, waiting } = jobQueue(services);
  if (!now.length && !waiting.length) return null;
  const lead = now[0]?.service || waiting[0].service;
  // With nothing running yet, the worker is busy with something else (a backup, core setup).
  const title = now.length
    ? names(now)
    : `${waiting.length === 1 ? `${lead.name} is` : `${waiting.length} apps are`} waiting their turn`;
  // The order opens only when there is one to show: something waiting behind another app.
  const toggle = now.length + waiting.length > 1 ? waiting.length : 0;
  if (!toggle)
    return (
      <section className="job-queue" aria-label="Apps at work" aria-live="polite">
        <Link to={`/apps/${lead.id}`} className="job-queue-lead">
          <Loader2 className="spin" />
          <span>{title}</span>
          <ArrowRight className="attention-arrow" />
        </Link>
      </section>
    );
  // Anywhere on the bar opens the order; only the arrow at its end opens the app at work.
  return (
    <section className="job-queue" aria-label="Apps at work" aria-live="polite">
      <div className="job-queue-head">
        <button type="button" className="job-queue-lead" aria-expanded={open} onClick={() => setOpen(!open)}>
          <Loader2 className="spin" />
          <span>{title}</span>
          <small className="job-queue-count">{toggle} waiting</small>
          <ChevronDown className="job-queue-chevron" aria-hidden="true" />
        </button>
        {now.length > 0 && (
          <Link
            to={`/apps/${lead.id}`}
            className="btn btn-ghost btn-sm btn-icon job-queue-open"
            aria-label={`Open ${lead.name}`}
            title={`Open ${lead.name}`}
          >
            <ArrowRight />
          </Link>
        )}
      </div>
      {open && <QueueList now={now} waiting={waiting} />}
    </section>
  );
}

/**
 * The Apps page's version: an install, retry or update running now keeps its own progress card,
 * with the waiting apps folded into it; anything else is the one-line notice.
 */
export function AppsJobQueue({ services }: { services: Service[] }) {
  const [open, setOpen] = useState(false);
  const { now, waiting } = jobQueue(services);
  const detailed = now.length === 1 && activeJob(now[0].service) ? now[0].service : null;
  if (!detailed) return <JobQueueNotice services={services} />;
  return (
    <JobProgress service={detailed} showName>
      {waiting.length > 0 && (
        <div className="job-queue-folded">
          <QueueToggle count={waiting.length} open={open} onToggle={() => setOpen(!open)} />
          {open && <QueueList now={now} waiting={waiting} />}
        </div>
      )}
    </JobProgress>
  );
}
