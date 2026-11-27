import { X } from 'lucide-react';
import { type ReactNode, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { api, type JobCancelResponse, type JobDetailResponse, postJsonApi, type Service } from '../../api';
import { Button } from '../../components/Button';
import { Collapsible } from '../../components/Layout';
import { errorText } from '../../lib/format';
import { useDashboard, useIsAdmin } from '../../state/dashboard';
import { DownloadStatus } from './InstallProgress';
import { stepLabel } from './steps';

/** Single-app jobs that get a progress card (an Apps-page install shows its batch card instead). */
const TRACKED = ['install', 'retry_setup', 'update'];
const ACTIVE = ['queued', 'running'];

/** An install's steps in the order the worker runs them; some apps skip a few. */
const STEPS = [
  'validate_service',
  'materialize_runtime',
  'validate_configuration',
  'pull_images',
  'resolve_digests',
  'start_service',
  'account_bootstrap',
  'account_verified',
  'verify_application',
  'configure_route',
  'authentik_ready',
  'verify_sign_in',
  'finalize',
  'stop_after_setup',
];

/** An update's stages, in order: the worker logs each as "<stage>: <what it is doing>". */
const UPDATE_STEPS = ['download_images', 'stage_definition', 'backup', 'apply_update', 'put_back'];
const UPDATE_LABEL: Record<string, string> = {
  download_images: 'Downloading the new version',
  stage_definition: 'Preparing its new settings',
  backup: 'Stopping it and backing up its data',
  apply_update: 'Starting the new version',
  put_back: 'Putting the old version back',
};

/** The app's single-app install, retry or update while it runs; null otherwise. */
export function activeJob(service: Service) {
  const job = service.last_job;
  return job && TRACKED.includes(job.action) && ACTIVE.includes(job.state) ? job : null;
}

/** Progress of one app's install, retry or update: a bar, the current step and live activity. */
export function JobProgress({
  service,
  showName = false,
  children,
}: {
  service: Service;
  showName?: boolean;
  /** Shown under the bar: the Apps page puts the apps waiting their turn here. */
  children?: ReactNode;
}) {
  const job = activeJob(service);
  const jobId = job?.id;
  const [detail, setDetail] = useState<JobDetailResponse | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const isAdmin = useIsAdmin();
  const { refresh } = useDashboard();

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    const load = () =>
      api<JobDetailResponse>(`/api/v1/jobs/${jobId}`)
        .then((result) => {
          if (!stopped) setDetail(result);
        })
        .catch(() => undefined);
    void load();
    const timer = window.setInterval(load, 2000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [jobId]);

  if (!job) return null;
  // Ignore an earlier job's answer until this job's first one arrives.
  const fresh = detail?.job.id === job.id ? detail : null;
  const current = fresh?.job || job;
  const events = fresh?.events || [];
  const updating = job.action === 'update';
  // An update names its stage in its events; an install in its step.
  const stages = events.filter((event) => event.event === 'stage').map((event) => event.detail.split(': ', 1)[0]);
  const step = updating ? stages[stages.length - 1] || '' : current.step_id || '';
  const steps = updating ? UPDATE_STEPS.slice(0, 4) : STEPS;
  // The worker logs each step as "<step>: <what it is doing>". The bar follows the furthest
  // step reached, so it never moves back when a step repeats.
  const furthest = Math.max(
    steps.indexOf(step),
    ...events.map((event) => steps.indexOf(event.detail.split(': ', 1)[0])),
  );
  const percent = Math.round(((furthest + 1) / steps.length) * 100);
  const download = fresh?.download;
  const log = events.map((event) => `${new Date(event.created_at).toLocaleTimeString()}  ${event.detail}`);
  const verb = updating ? 'Updating' : job.action === 'install' ? 'Installing' : 'Setting up';
  const queued = current.state === 'queued';
  // A queued update downloads before its turn; it shows that download instead of the bar.
  const downloading =
    Boolean(download && download.state !== 'done') &&
    (updating ? queued || step === 'download_images' : step === 'pull_images');
  // Until it stops the app, an update can be cancelled with nothing changed.
  const canCancel = isAdmin && updating && (queued || !step || step === 'download_images');
  const status = queued
    ? downloading
      ? 'Downloading while it waits its turn'
      : 'Waiting its turn'
    : updating
      ? UPDATE_LABEL[step] || 'Starting'
      : stepLabel(step);

  const cancel = async () => {
    setCancelling(true);
    try {
      const result = await postJsonApi<JobCancelResponse>(`/api/v1/jobs/${job.id}/cancel`, {});
      toast.success(
        result.state === 'cancelled'
          ? `${service.name}'s update is cancelled; nothing changed.`
          : `Stopping ${service.name}'s update; nothing about it changes.`,
      );
    } catch (cause) {
      toast.error(errorText(cause));
    } finally {
      setCancelling(false);
      void refresh();
    }
  };

  return (
    <section className="card install-progress" aria-live="polite" aria-label={`${service.name} progress`}>
      <header className="install-header">
        <div>
          <b>{showName ? `${verb} ${service.name}` : verb}</b>
          <p>{status}</p>
        </div>
        {canCancel && (
          <Button size="sm" variant="ghost" icon={X} loading={cancelling} onClick={() => void cancel()}>
            Cancel
          </Button>
        )}
      </header>
      {downloading && download ? (
        <DownloadStatus download={download} />
      ) : (
        <div
          className="progress"
          role="progressbar"
          aria-label={`${service.name} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <span style={{ width: `${percent}%` }} />
        </div>
      )}
      {children}
      {log.length > 0 && (
        <Collapsible title="Live activity">
          <pre className="log">{log.slice(-20).join('\n')}</pre>
        </Collapsible>
      )}
    </section>
  );
}
