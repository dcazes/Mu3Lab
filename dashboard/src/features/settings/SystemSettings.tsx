import { AlertTriangle, CheckCircle2, Copy, Network, RefreshCw, Rocket, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { api, postApi, postJsonApi, type Mu3LabUpdate, type SystemResponse, type TailscaleStatus } from '../../api';
import { Button, ExternalButton, LinkButton } from '../../components/Button';
import { copyText } from '../../components/CopyField';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card, Facts, PageHeader } from '../../components/Layout';
import { Badge, Dot, StateBadge } from '../../components/Status';
import { bytes } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';
import { JobDialog, JobRow } from '../activity/JobDialog';
import { ResourceCharts } from '../home/ResourceCharts';

/** One line for the Server card's header, so it says something useful while folded. */
function serverSummary(system: SystemResponse): string {
  const part = (label: string, used: number, total: number) => `${label} ${bytes(used)} of ${bytes(total)}`;
  return [
    part('Memory', system.memory.used, system.memory.total),
    part('Disk', system.disk.used, system.disk.total),
    system.gpu ? part('Graphics', system.gpu.used, system.gpu.total) : '',
    `CPU ${Math.round(system.cpu_percent)}%`,
  ]
    .filter(Boolean)
    .join(' · ');
}

/** Setup normally finishes in the installer; this shows only when a setup step has failed. */
function SetupProblem() {
  const { data, refresh } = useDashboard();
  const { provisioning, core, identity } = data;
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const failedJob = core.current_job?.state === 'failed';
  if (!provisioning.available || provisioning.complete || !(provisioning.blocked || failedJob)) return null;
  const next = provisioning.next_action;
  return (
    <Callout
      tone="danger"
      icon={AlertTriangle}
      title="A setup step didn’t finish"
      action={
        next?.kind === 'link' && next.href ? (
          <LinkButton to={next.href}>{next.label}</LinkButton>
        ) : next?.kind === 'job' && next.endpoint && identity.writes_enabled ? (
          <Button
            icon={Rocket}
            loading={pending === 'setup'}
            onClick={async () => {
              if (!(await confirm({ title: `${next.label}?`, description: 'Existing apps and accounts are kept.' })))
                return;
              await run('setup', () => postApi(next.endpoint!), 'Setup started');
              void refresh();
            }}
          >
            {next.label}
          </Button>
        ) : undefined
      }
    >
      {provisioning.blocked?.detail || core.current_job?.detail || core.next_action}
      {next?.kind === 'bootstrap' && (
        <>
          {' '}
          Run <code>./install.sh</code> on the server to finish it.
        </>
      )}
    </Callout>
  );
}

function Tailnet({ status }: { status: TailscaleStatus }) {
  const tone = status.state === 'connected' ? 'green' : status.state === 'disconnected' ? 'red' : 'gray';
  const label =
    status.state === 'connected' ? 'Connected' : status.state === 'disconnected' ? 'Disconnected' : 'Unavailable';
  return (
    <Card
      title={
        <>
          <Network className="inline-icon" /> Private network
        </>
      }
      description="Tailscale provides private access to Mu3Lab for authorized devices on your network."
      actions={
        <ExternalButton size="sm" href="https://login.tailscale.com/admin">
          Tailscale admin
        </ExternalButton>
      }
    >
      <Facts
        items={[
          {
            label: 'Status',
            value: (
              <Badge tone={tone}>
                <Dot tone={tone} />
                {label}
              </Badge>
            ),
          },
          {
            label: 'Address',
            value: status.dns_name ? (
              <span className="inline">
                <code>{status.dns_name}</code>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={Copy}
                  aria-label="Copy tailnet address"
                  onClick={() => void copyText(status.dns_name, 'Tailnet address copied')}
                />
              </span>
            ) : (
              'Not assigned'
            ),
          },
          {
            label: 'Published ports',
            value:
              status.serve.state === 'unavailable' ? (
                'Unavailable'
              ) : status.serve.ports.length ? (
                <span title={status.serve.ports.join(', ')}>{status.serve.ports.length} private HTTPS routes</span>
              ) : (
                'None'
              ),
          },
        ]}
      />
    </Card>
  );
}

export function Mu3LabUpdates() {
  const { data, refresh } = useDashboard();
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const loaded = useApi<Mu3LabUpdate>('/api/v1/system/update');
  const [checked, setChecked] = useState<Mu3LabUpdate | null>(null);
  const update = checked || loaded.data;
  const job = data.jobs.jobs.find((item) => item.service_id === 'mu3lab');
  const running = Boolean(job && ['queued', 'running'].includes(job.state));
  const finished = job?.state === 'succeeded' && job.detail.startsWith('Mu3Lab updated');
  const check = () =>
    run('check', async () => setChecked(await api<Mu3LabUpdate>('/api/v1/system/update?refresh=true')));
  const apply = async () => {
    if (
      !(await confirm({
        title: 'Update Mu3Lab?',
        description:
          'Mu3Lab downloads the update from GitHub, installs it, and restarts the dashboard. Your apps keep running, and their versions don’t change: apps with a newly tested version show “Update ready” afterwards.',
        confirmLabel: 'Update',
      }))
    )
      return;
    const result = await run('update', () => postJsonApi('/api/v1/system/update', {}), 'Updating Mu3Lab');
    if (result !== undefined) void refresh();
  };
  // An older control plane, mid-restart, may not know this endpoint's shape yet.
  const changes = update?.changes ?? [];
  const more = changes.length - 8;
  return (
    <Card
      title="Mu3Lab updates"
      description={
        update ? `Version ${update.version} · ${update.commit} (${update.date})` : 'Checking the installed version…'
      }
      actions={
        <>
          <LinkButton size="sm" variant="ghost" icon={Sparkles} to="/settings/whats-new">
            What’s new
          </LinkButton>
          <Button
            size="sm"
            icon={RefreshCw}
            loading={pending === 'check'}
            disabled={running}
            onClick={() => void check()}
          >
            Check now
          </Button>
        </>
      }
    >
      {finished ? (
        <Callout
          tone="success"
          icon={CheckCircle2}
          title="Mu3Lab was updated"
          action={<Button onClick={() => window.location.reload()}>Reload</Button>}
        >
          {job?.detail}
        </Callout>
      ) : running ? (
        <p>Updating Mu3Lab… The dashboard restarts when it’s done.</p>
      ) : !update ? (
        loaded.error && <p className="muted">{loaded.error}</p>
      ) : (
        <div className="stack">
          {update.last_update?.state === 'rolled_back' && (
            <Callout tone="warning" title={`Mu3Lab went back to ${update.last_update.from || 'the previous version'}`}>
              {update.last_update.detail} Nothing else needs doing; you can try the update again later.
            </Callout>
          )}
          {update.last_update?.state === 'needs_attention' && (
            <Callout tone="danger" title="The last update needs your attention">
              {update.last_update.detail}
            </Callout>
          )}
          {job?.state === 'failed' && (
            <Callout tone="warning" title="The last update didn’t finish">
              {job.detail}
            </Callout>
          )}
          {update.available ? (
            <>
              <p>{update.behind === 1 ? 'One change is' : `${update.behind} changes are`} ready to install:</p>
              <ul className="change-list">
                {changes.slice(0, 8).map((change) => (
                  <li key={change}>{change}</li>
                ))}
                {more > 0 && <li className="muted">and {more} more</li>}
              </ul>
            </>
          ) : (
            !update.blocked_reason && <p>Mu3Lab is up to date.</p>
          )}
          {update.blocked_reason && <p className="muted">{update.blocked_reason}</p>}
          {update.available && !update.blocked_reason && (
            <div className="button-row">
              <Button variant="primary" icon={Rocket} loading={pending === 'update'} onClick={() => void apply()}>
                Update Mu3Lab
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export function SystemSettings() {
  const { data } = useDashboard();
  const { system } = data;
  const [jobId, setJobId] = useState<string | null>(null);
  const tailscale: TailscaleStatus = system.tailscale || {
    state: system.tailnet_dns_name ? 'connected' : 'unavailable',
    backend_state: '',
    online: Boolean(system.tailnet_dns_name),
    dns_name: system.tailnet_dns_name,
    detail: '',
    serve: { state: 'unavailable', ports: [] },
  };
  return (
    <>
      <PageHeader title="System" description="Keep Mu3Lab up to date, and monitor server health and network access." />
      <SetupProblem />
      <Mu3LabUpdates />
      {/* Its one-line summary says enough most days; the per-app table opens on demand. */}
      <Card title="Server" description={serverSummary(system)} collapsible defaultOpen={false}>
        <ResourceCharts detailed />
        <Facts
          items={[
            {
              label: 'Docker',
              value: <StateBadge state={system.docker_ready ? 'ready' : 'failed'} />,
            },
            { label: 'Data location', value: <code>{system.runtime_root}</code> },
          ]}
        />
      </Card>
      <Tailnet status={tailscale} />
      <Card
        title="Activity"
        description="Recent installation, maintenance, and configuration jobs."
        flush
        collapsible
        defaultOpen={false}
      >
        {data.jobs.jobs.length ? (
          <div className="rows">
            {data.jobs.jobs.slice(0, 15).map((job) => (
              <JobRow key={job.id} job={job} onOpen={() => setJobId(job.id)} />
            ))}
          </div>
        ) : (
          <p className="muted card-pad">No activity yet.</p>
        )}
      </Card>
      <JobDialog jobId={jobId} onClose={() => setJobId(null)} />
    </>
  );
}
