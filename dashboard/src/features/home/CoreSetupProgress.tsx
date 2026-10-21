import type { CoreAppProgress } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Badge } from '../../components/Status';
import type { Tone } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { DownloadStatus } from '../apps/InstallProgress';

const ACTIVE = new Set(['queued', 'running']);

const STATE: Record<CoreAppProgress['state'], { label: string; tone: Tone }> = {
  waiting: { label: 'Waiting to download', tone: 'gray' },
  downloading: { label: 'Downloading', tone: 'blue' },
  downloaded: { label: 'Downloaded', tone: 'gray' },
  installing: { label: 'Installing', tone: 'blue' },
  ready: { label: 'Ready', tone: 'green' },
  failed: { label: 'Didn’t finish', tone: 'red' },
};

/** Whether the core apps are still downloading or installing after setup handed off. */
export function useCoreSetupActive() {
  const { data } = useDashboard();
  const job = data.core.current_job;
  return Boolean(job && ACTIVE.has(job.state) && data.core.apps?.length);
}

/** Setup hands off to the dashboard while the core AI apps download and install; this shows how far they are. */
export function CoreSetupProgress() {
  const { data } = useDashboard();
  const active = useCoreSetupActive();
  const apps = data.core.apps || [];
  if (!active) return null;
  const ready = apps.filter((app) => app.state === 'ready').length;
  return (
    <section className="core-setup" aria-labelledby="core-setup-heading">
      <div className="section-title">
        <h2 id="core-setup-heading">Setting up your AI apps</h2>
        <span className="get-started-count">
          {ready} of {apps.length} ready
        </span>
      </div>
      <p className="core-setup-note">
        They download and install by themselves, which can take a while. Carry on with the steps below meanwhile.
      </p>
      <ul className="core-setup-list">
        {apps.map((app) => (
          <li key={app.service_id} className="core-setup-item">
            <AppIcon id={app.service_id} size="sm" />
            <div className="core-setup-body">
              <span className="core-setup-name">{app.name}</span>
              {app.download && <DownloadStatus download={app.download} />}
            </div>
            <Badge tone={STATE[app.state].tone}>{STATE[app.state].label}</Badge>
          </li>
        ))}
      </ul>
    </section>
  );
}
