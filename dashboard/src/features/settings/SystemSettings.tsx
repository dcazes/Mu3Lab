import { CheckCircle2, Copy, Network, Rocket } from 'lucide-react';
import { useState } from 'react';
import { postApi, putJsonApi, type SystemConfig, type TailscaleStatus } from '../../api';
import { Button, ExternalButton, LinkButton } from '../../components/Button';
import { copyText } from '../../components/CopyField';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card, Facts, PageHeader } from '../../components/Layout';
import { Badge, Dot, StateBadge } from '../../components/Status';
import { bytes, duration } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';
import { JobDialog, JobRow } from '../activity/JobDialog';

function Meter({ label, percent, detail }: { label: string; percent: number; detail: string }) {
  const tone = percent > 90 ? 'red' : percent > 75 ? 'amber' : 'green';
  return (
    <div className="meter">
      <div className="meter-label">
        <span>{label}</span>
        <b>{Math.round(percent)}%</b>
      </div>
      <div className={`meter-bar meter-${tone}`}>
        <span style={{ width: `${Math.min(100, Math.max(2, percent))}%` }} />
      </div>
      <small>{detail}</small>
    </div>
  );
}

function Setup() {
  const { data, refresh } = useDashboard();
  const { provisioning, core, identity } = data;
  const confirm = useConfirm();
  const { pending, run } = useAction();
  if (!provisioning.available) return null;
  const progress = provisioning.progress || {
    completed: provisioning.phases.filter((phase) => ['verified', 'skipped'].includes(phase.actual_state)).length,
    total: provisioning.phases.length,
  };
  if (provisioning.complete)
    return (
      <Callout tone="success" icon={CheckCircle2} title="Mu3Lab is fully set up">
        All {progress.total} setup steps are verified.
      </Callout>
    );
  const phase = provisioning.waiting || provisioning.blocked;
  const next = provisioning.next_action;
  return (
    <Card
      title="Finish setup"
      description={phase?.detail || core.next_action}
      actions={
        <span className="install-count">
          {progress.completed}/{progress.total}
        </span>
      }
    >
      <ol className="checklist">
        {provisioning.phases.map((item) => {
          const done = ['verified', 'skipped'].includes(item.actual_state);
          const failed = item.actual_state === 'failed';
          return (
            <li key={item.phase_id} className={done ? 'done' : failed ? 'failed' : ''}>
              <Dot tone={done ? 'green' : failed ? 'red' : 'gray'} />
              <span>{item.label}</span>
              {!done && <small>{item.actual_state.replaceAll('_', ' ')}</small>}
            </li>
          );
        })}
      </ol>
      {core.current_job && ['queued', 'running', 'failed'].includes(core.current_job.state) && (
        <p className="muted">{core.current_job.detail}</p>
      )}
      <div className="button-row">
        {next?.kind === 'link' && next.href && (
          <LinkButton variant="primary" to={next.href}>
            {next.label}
          </LinkButton>
        )}
        {next?.kind === 'job' && next.endpoint && identity.writes_enabled && (
          <Button
            variant="primary"
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
        )}
        {next?.kind === 'bootstrap' && (
          <p className="muted">
            Run <code>./install.sh</code> on the server to finish this step.
          </p>
        )}
      </div>
    </Card>
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
      description="Tailscale keeps Mu3Lab reachable only from your own devices."
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

function Compute() {
  const config = useApi<SystemConfig>('/api/v1/system/config');
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const modes = ['auto', 'cpu', 'nvidia', 'amd'] as const;
  const select = async (mode: SystemConfig['compute_mode']) => {
    if (
      !(await confirm({
        title: `Use ${mode === 'auto' ? 'automatic' : mode.toUpperCase()} acceleration?`,
        description: 'Restart Ollama afterwards to apply it to the running container.',
      }))
    )
      return;
    await run('compute', () => putJsonApi('/api/v1/system/config', { compute_mode: mode }), 'Compute mode saved');
    void config.reload();
  };
  return (
    <Card
      title="AI acceleration"
      description={`Used by local AI models. Automatic currently uses ${config.data?.resolved_compute_mode?.toUpperCase() || '…'}.`}
    >
      <div className="segmented" role="radiogroup" aria-label="Compute mode">
        {modes.map((mode) => (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={config.data?.compute_mode === mode}
            disabled={
              Boolean(pending) || !config.data || (mode !== 'auto' && !config.data.available_modes.includes(mode))
            }
            onClick={() => void select(mode)}
          >
            {mode === 'auto' ? 'Automatic' : mode.toUpperCase()}
          </button>
        ))}
      </div>
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
      <PageHeader title="System" description="The health of the machine running Mu3Lab." />
      <Setup />
      <Card title="Server">
        <div className="meters">
          <Meter label="CPU" percent={system.cpu_percent} detail={`Up ${duration(system.uptime_seconds)}`} />
          <Meter
            label="Memory"
            percent={system.memory.percent}
            detail={`${bytes(system.memory.used)} of ${bytes(system.memory.total)}`}
          />
          <Meter
            label="Disk"
            percent={system.disk.percent}
            detail={`${bytes(system.disk.total - system.disk.used)} free`}
          />
        </div>
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
      <Compute />
      <Card title="Activity" description="Background jobs Mu3Lab ran recently." flush>
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
