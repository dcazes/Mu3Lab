import { RotateCcw, X } from 'lucide-react';
import { type Job, type JobDetailResponse, postApi } from '../../api';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { StateBadge } from '../../components/Status';
import { humanize, relativeTime } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

/** A readable subject for a job: the app's name, or its chat integration. */
export function useJobSubject() {
  const { data } = useDashboard();
  return (serviceId: string) => {
    const [kind, id] = serviceId.includes(':') ? serviceId.split(':', 2) : ['', serviceId];
    const service = data.services.services.find((item) => (kind === 'mcp' ? id.startsWith(item.id) : item.id === id));
    if (kind === 'mcp') return `${service?.name || id} chat integration`;
    if (kind === 'provider') return `${humanize(id)} provider`;
    if (serviceId === 'core-suite') return 'Core platform';
    return service?.name || humanize(serviceId);
  };
}

export function JobRow({ job, onOpen, showService = true }: { job: Job; onOpen: () => void; showService?: boolean }) {
  const subject = useJobSubject();
  return (
    <button type="button" className="row row-button" onClick={onOpen}>
      <span className="row-text">
        <b>{showService ? `${subject(job.service_id)} · ${humanize(job.action)}` : humanize(job.action)}</b>
        <small>{job.detail || humanize(job.step_id)}</small>
      </span>
      <small className="row-time">{relativeTime(job.updated_at || job.created_at)}</small>
      <StateBadge state={job.state} />
    </button>
  );
}

export function JobDialog({ jobId, onClose }: { jobId: string | null; onClose: () => void }) {
  const detail = useApi<JobDetailResponse>(jobId ? `/api/v1/jobs/${jobId}` : null, { interval: 3000 });
  const { pending, run } = useAction();
  const subject = useJobSubject();
  const job = detail.data?.job;
  const act = async (action: 'retry' | 'cancel') => {
    if (!job) return;
    await run(
      action,
      () => postApi(`/api/v1/jobs/${job.id}/${action}`),
      action === 'retry' ? 'Retry started' : 'Cancelling',
    );
    void detail.reload();
  };
  return (
    <Dialog
      open={Boolean(jobId)}
      onClose={onClose}
      size="lg"
      title={job ? `${subject(job.service_id)} · ${humanize(job.action)}` : 'Job'}
      description={job ? <StateBadge state={job.state} /> : undefined}
      footer={
        job && (
          <>
            {['queued', 'running'].includes(job.state) && (
              <Button icon={X} loading={pending === 'cancel'} onClick={() => void act('cancel')}>
                Cancel job
              </Button>
            )}
            {['failed', 'waiting_for_confirmation'].includes(job.state) && (
              <Button
                variant="primary"
                icon={RotateCcw}
                loading={pending === 'retry'}
                onClick={() => void act('retry')}
              >
                Retry
              </Button>
            )}
          </>
        )
      }
    >
      {detail.error && <p className="error-text">{detail.error}</p>}
      {job && (
        <>
          <p>{job.detail}</p>
          {job.error_code && <p className="muted">Error code: {job.error_code}</p>}
          <pre className="log">
            {(detail.data?.events || [])
              .map((event) => `${new Date(event.created_at).toLocaleTimeString()}  ${event.detail || event.event}`)
              .join('\n') || 'No events recorded.'}
          </pre>
        </>
      )}
    </Dialog>
  );
}
