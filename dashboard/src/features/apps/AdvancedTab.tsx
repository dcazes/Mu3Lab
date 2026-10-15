import { ArrowUpCircle, Copy, Download, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, postJsonApi, type Service, type ServiceLogsResponse, type UpdateResponse } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { copyText } from '../../components/CopyField';
import { useConfirm } from '../../components/Dialog';
import { Card, Facts } from '../../components/Layout';
import { StateBadge } from '../../components/Status';
import { humanize } from '../../lib/format';
import { signInSummary } from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { useAction } from '../../lib/useAction';
import { useDashboard } from '../../state/dashboard';
import { JobDialog, JobRow } from '../activity/JobDialog';
import { BackupsCard } from './BackupsCard';

function Logs({ service }: { service: Service }) {
  const [lines, setLines] = useState<string[] | null>(null);
  const [container, setContainer] = useState('');
  const [tail, setTail] = useState(200);
  const { pending, run } = useAction();
  const load = () =>
    run('logs', async () => {
      const query = `tail=${tail}${container ? `&container=${encodeURIComponent(container)}` : ''}`;
      setLines((await api<ServiceLogsResponse>(`/api/v1/services/${service.id}/logs?${query}`)).lines);
    });
  const download = () => {
    const url = URL.createObjectURL(new Blob([(lines || []).join('\n')], { type: 'text/plain' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${service.id}-logs.txt`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Card
      title="Logs"
      description="Recent output with secrets redacted."
      actions={
        <>
          <select
            className="select-sm"
            aria-label="Container"
            value={container}
            onChange={(event) => setContainer(event.target.value)}
          >
            <option value="">All containers</option>
            {(service.containers || []).map((item) => (
              <option key={item.service} value={item.service}>
                {item.service}
              </option>
            ))}
          </select>
          <select
            className="select-sm"
            aria-label="Lines"
            value={tail}
            onChange={(event) => setTail(Number(event.target.value))}
          >
            {[50, 200, 500].map((value) => (
              <option key={value} value={value}>
                {value} lines
              </option>
            ))}
          </select>
          <Button size="sm" icon={RefreshCw} loading={pending === 'logs'} onClick={() => void load()}>
            {lines ? 'Refresh' : 'Load logs'}
          </Button>
        </>
      }
    >
      {lines && (
        <>
          <pre className="log log-tall">{lines.join('\n') || 'No recent log lines.'}</pre>
          <div className="button-row">
            <Button size="sm" icon={Copy} onClick={() => void copyText(lines.join('\n'), 'Logs copied')}>
              Copy
            </Button>
            <Button size="sm" icon={Download} onClick={download}>
              Download
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}

function Updates({ service }: { service: Service }) {
  const confirm = useConfirm();
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const installed = service.update?.installed_version ?? '';
  // A local comparison with the release Mu3Lab approves, so it loads straight away.
  const { data: update, reload } = useApi<UpdateResponse>(
    service.update?.repository ? `/api/v1/services/${service.id}/updates` : null,
  );
  // An update, a restore or a newer Mu3Lab changed what is installed or approved.
  const approved = service.update?.approved_version ?? '';
  const seen = useRef(`${installed} ${approved}`);
  useEffect(() => {
    if (seen.current === `${installed} ${approved}`) return;
    seen.current = `${installed} ${approved}`;
    void reload();
  }, [installed, approved, reload]);
  if (!service.update?.repository) return null;
  const target = update?.supporting_only
    ? `updated supporting services for ${update.approved_version}`
    : update?.approved_version;
  const apply = async () => {
    if (!update) return;
    if (
      !(await confirm({
        title: update.supporting_only
          ? `Update ${service.name}’s supporting services?`
          : `Update ${service.name} to ${update.approved_version}?`,
        description: `Mu3Lab downloads the new version, stops ${service.name}, saves a backup of its data, then starts the new version. If it doesn't start properly, Mu3Lab puts back the backup and the current version automatically. ${service.name} is unavailable for a few minutes.`,
        confirmLabel: 'Update',
      }))
    )
      return;
    const result = await run(
      'update',
      () => postJsonApi(`/api/v1/services/${service.id}/actions`, { action: 'update' }),
      `Updating ${service.name}`,
    );
    if (result === undefined) return;
    void refresh();
  };
  return (
    <Card
      title="Updates"
      description={
        service.stage === 'optional'
          ? `Installed version ${installed || 'unknown'}. Mu3Lab offers a new version once it has been tested with Mu3Lab.`
          : `Version ${installed || 'unknown'}. Updated together with Mu3Lab.`
      }
    >
      {update && service.stage === 'optional' && installed && (
        <div className="stack">
          <p>
            {update.update_available
              ? `A tested update is ready: ${target}.`
              : `You’re on the latest tested version (${update.installed_version}).`}
          </p>
          {update.update_available && (update.removed_services?.length ?? 0) > 0 && (
            <p className="muted">This update removes: {update.removed_services?.join(', ')}.</p>
          )}
          {update.update_available && (update.added_services?.length ?? 0) > 0 && (
            <p className="muted">This update adds: {update.added_services?.join(', ')}.</p>
          )}
          {update.update_available && !update.update_enabled && update.blocked_reason && (
            <p className="muted">{update.blocked_reason}</p>
          )}
          <div className="button-row">
            {update.update_enabled && (
              <Button
                variant="primary"
                icon={ArrowUpCircle}
                loading={pending === 'update'}
                onClick={() => void apply()}
              >
                {update.supporting_only ? 'Update' : `Update to ${update.approved_version}`}
              </Button>
            )}
            {update.update_available && update.release_url && !update.supporting_only && (
              <ExternalButton href={update.release_url}>Release notes</ExternalButton>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

export function AdvancedTab({ service }: { service: Service }) {
  const { data } = useDashboard();
  const [jobId, setJobId] = useState<string | null>(null);
  const jobs = data.jobs.jobs.filter((job) => job.service_id === service.id).slice(0, 8);
  return (
    <div className="stack">
      {service.compose_present && <Logs service={service} />}
      <Card title="Activity" flush>
        {jobs.length ? (
          <div className="rows">
            {jobs.map((job) => (
              <JobRow key={job.id} job={job} showService={false} onOpen={() => setJobId(job.id)} />
            ))}
          </div>
        ) : (
          <p className="muted card-pad">No recorded activity.</p>
        )}
      </Card>
      <Updates service={service} />
      {service.stage === 'optional' && <BackupsCard service={service} />}
      <Card title="Technical details">
        <Facts
          items={[
            { label: 'Health', value: humanize(service.health_state) },
            { label: 'Private route', value: humanize(service.route_ready ? 'ready' : 'not_ready') },
            { label: 'Sign-in method', value: signInSummary(service).label },
            { label: 'Lifecycle', value: humanize(service.lifecycle) },
            ...(service.containers?.length
              ? [
                  {
                    label: 'Containers',
                    value: (
                      <div className="rows compact">
                        {service.containers.map((container) => (
                          <div className="row" key={container.name}>
                            <span className="row-text">
                              <b>{container.service}</b>
                              <small>{container.image}</small>
                            </span>
                            <StateBadge
                              state={
                                container.health && container.health !== 'unknown' ? container.health : container.state
                              }
                            />
                          </div>
                        ))}
                      </div>
                    ),
                  },
                ]
              : []),
          ]}
        />
      </Card>
      <JobDialog jobId={jobId} onClose={() => setJobId(null)} />
    </div>
  );
}
