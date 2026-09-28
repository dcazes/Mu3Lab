import { Copy, Download, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { api, type Service, type ServiceLogsResponse, type UpdateResponse } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { copyText } from '../../components/CopyField';
import { Card, Facts } from '../../components/Layout';
import { StateBadge } from '../../components/Status';
import { humanize } from '../../lib/format';
import { signInSummary } from '../../lib/services';
import { useAction } from '../../lib/useAction';
import { useDashboard } from '../../state/dashboard';
import { JobDialog, JobRow } from '../activity/JobDialog';

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
  const [update, setUpdate] = useState<UpdateResponse | null>(null);
  const { pending, run } = useAction();
  if (!service.update?.repository) return null;
  return (
    <Card
      title="Updates"
      description={`Installed version ${service.update.current_version || 'unknown'}.`}
      actions={
        <Button
          size="sm"
          loading={pending === 'check'}
          onClick={() =>
            void run('check', async () =>
              setUpdate(await api<UpdateResponse>(`/api/v1/services/${service.id}/updates`)),
            )
          }
        >
          Check for updates
        </Button>
      }
    >
      {update && (
        <div className="stack">
          <p>
            {update.update_available
              ? `${update.latest_version} is available.`
              : `You’re on the latest release (${update.latest_version || update.current_version}).`}
          </p>
          {update.update_available && <p className="muted">{update.blocked_reason}</p>}
          {update.release_url && <ExternalButton href={update.release_url}>Release notes</ExternalButton>}
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
      <Card title="Technical details">
        <Facts
          items={[
            { label: 'Health', value: humanize(service.health_state) },
            { label: 'Private route', value: humanize(service.route_state) },
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
