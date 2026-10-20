import { useEffect, useState } from 'react';
import { api, type JobDetailResponse, type Service } from '../../api';
import { Collapsible } from '../../components/Layout';
import { humanize } from '../../lib/format';
import { DownloadStatus } from './InstallProgress';

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

/** The app's single-app install, retry or update while it runs; null otherwise. */
export function activeJob(service: Service) {
  const job = service.last_job;
  return job && TRACKED.includes(job.action) && ACTIVE.includes(job.state) ? job : null;
}

/** Progress of one app's install, retry or update: a bar, the current step and live activity. */
export function JobProgress({ service, showName = false }: { service: Service; showName?: boolean }) {
  const job = activeJob(service);
  const jobId = job?.id;
  const [detail, setDetail] = useState<JobDetailResponse | null>(null);

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
  const step = current.step_id || '';
  const events = fresh?.events || [];
  // The worker logs each step as "<step>: <what it is doing>". The bar follows the furthest
  // step reached, so it never moves back when a step repeats.
  const furthest = Math.max(
    STEPS.indexOf(step),
    ...events.map((event) => STEPS.indexOf(event.detail.split(': ', 1)[0])),
  );
  const percent = Math.round(((furthest + 1) / STEPS.length) * 100);
  const message = [...events].reverse().find((event) => step && event.detail.startsWith(`${step}: `));
  const download = fresh?.download;
  const log = events.map((event) => `${new Date(event.created_at).toLocaleTimeString()}  ${event.detail}`);
  const verb = job.action === 'update' ? 'Updating' : 'Setting up';

  return (
    <section className="card install-progress" aria-live="polite" aria-label={`${service.name} progress`}>
      <header className="install-header">
        <div>
          <b>{showName ? `${verb} ${service.name}` : verb}</b>
          <p>
            {current.state === 'queued'
              ? 'Waiting for the worker to start'
              : message
                ? message.detail.slice(step.length + 2)
                : humanize(step || 'starting')}
          </p>
        </div>
      </header>
      {step === 'pull_images' && download && download.state !== 'done' ? (
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
      {log.length > 0 && (
        <Collapsible title="Live activity">
          <pre className="log">{log.slice(-20).join('\n')}</pre>
        </Collapsible>
      )}
    </section>
  );
}
