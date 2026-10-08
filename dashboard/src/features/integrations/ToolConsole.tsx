import { Play } from 'lucide-react';
import { useState } from 'react';
import { api, type Job, type McpServer, postApi, postJsonApi } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { StateBadge } from '../../components/Status';
import { relativeTime } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { JobDialog } from '../activity/JobDialog';

interface Call {
  id: number;
  tool_name: string;
  source: string;
  outcome: string;
  duration_ms: number;
  created_at: string;
}

interface Updates {
  current_version: string;
  reviewed_update: { version: string; release_url: string; notes?: string } | null;
}

/** Operator tools for testing a connection by hand; write inputs are encrypted until the approval reaches an outcome. */
export function ToolConsole({ server }: { server: McpServer }) {
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [toolId, setToolId] = useState('');
  const [input, setInput] = useState('{}');
  const [result, setResult] = useState('');
  const [calls, setCalls] = useState<Call[] | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [updates, setUpdates] = useState<Updates | null>(null);
  const tool = server.tools.find((item) => item.id === toolId);
  const base = `/api/v1/mcp/servers/${server.id}`;

  const execute = () =>
    run('run', async () => {
      if (!tool) return;
      const args = JSON.parse(input) as unknown;
      const path = `${base}/tools/${encodeURIComponent(tool.id)}`;
      const prepared = await postJsonApi<{ confirmation_required: boolean; confirmation_token: string }>(
        `${path}/prepare`,
        { arguments: args },
      );
      if (
        prepared.confirmation_required &&
        !(await confirm({ title: `Run ${tool.title}?`, description: <code>{input}</code>, confirmLabel: 'Run' }))
      )
        return;
      const response = await postJsonApi<{ ok: boolean; result: unknown; outcome: string }>(`${path}/execute`, {
        arguments: args,
        confirmation_token: prepared.confirmation_token,
      });
      setResult(JSON.stringify(response.result, null, 2));
      if (!response.ok) throw new Error(`Tool returned ${response.outcome}.`);
    });

  const update = async () => {
    if (!updates?.reviewed_update) return;
    if (
      !(await confirm({
        title: `Update to ${updates.reviewed_update.version}?`,
        description: 'The previous version is restored automatically if verification fails.',
        confirmLabel: 'Update',
      }))
    )
      return;
    await run('update', () => postApi(`${base}/update`), 'Update started');
  };

  return (
    <div className="console">
      {server.state === 'live' && server.tools.length > 0 && (
        <div className="stack">
          <h3>Test a tool</h3>
          <div className="field-row">
            <label className="field">
              <span>Tool</span>
              <select value={toolId} onChange={(event) => (setToolId(event.target.value), setResult(''))}>
                <option value="">Choose a tool…</option>
                {server.tools.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title || item.id}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {tool && (
            <>
              <label className="field">
                <span>Input (JSON)</span>
                <textarea
                  spellCheck={false}
                  rows={5}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                />
              </label>
              <details className="schema">
                <summary>Input schema</summary>
                <pre className="log">{JSON.stringify(tool.parameters || {}, null, 2)}</pre>
              </details>
              <div>
                <Button variant="primary" icon={Play} loading={pending === 'run'} onClick={() => void execute()}>
                  Run
                </Button>
              </div>
              {result && (
                <pre className="log" aria-label="Tool result">
                  {result}
                </pre>
              )}
            </>
          )}
        </div>
      )}
      <div className="button-row">
        <Button
          size="sm"
          onClick={() =>
            void run('calls', async () => setCalls((await api<{ calls: Call[] }>(`${base}/activity`)).calls))
          }
        >
          Action history
        </Button>
        <Button
          size="sm"
          onClick={() => void run('jobs', async () => setJobs((await api<{ jobs: Job[] }>(`${base}/jobs`)).jobs))}
        >
          Connection jobs
        </Button>
        <Button
          size="sm"
          onClick={() => void run('updates', async () => setUpdates(await api<Updates>(`${base}/updates`)))}
        >
          Check for updates
        </Button>
      </div>
      {updates && (
        <p className="muted">
          Version {updates.current_version}.{' '}
          {updates.reviewed_update ? (
            <>
              {updates.reviewed_update.version} is available.{' '}
              <ExternalButton size="sm" href={updates.reviewed_update.release_url}>
                Release notes
              </ExternalButton>{' '}
              <Button size="sm" variant="primary" disabled={server.state !== 'live'} onClick={() => void update()}>
                Update
              </Button>
            </>
          ) : (
            'No newer reviewed version.'
          )}
        </p>
      )}
      {calls && (
        <div className="rows">
          {calls.length ? (
            calls.map((call) => (
              <div className="row" key={call.id}>
                <span className="row-text">
                  <b>{call.tool_name}</b>
                  <small>
                    {call.source} · {call.duration_ms} ms
                  </small>
                </span>
                <small className="row-time">{relativeTime(call.created_at)}</small>
                <StateBadge state={call.outcome} />
              </div>
            ))
          ) : (
            <p className="muted">No tool calls recorded.</p>
          )}
        </div>
      )}
      {jobs && (
        <div className="rows">
          {jobs.length ? (
            jobs.map((job) => (
              <button type="button" className="row row-button" key={job.id} onClick={() => setJobId(job.id)}>
                <span className="row-text">
                  <b>{job.action}</b>
                  <small>{job.detail}</small>
                </span>
                <small className="row-time">{relativeTime(job.created_at)}</small>
                <StateBadge state={job.state} />
              </button>
            ))
          ) : (
            <p className="muted">No connection jobs recorded.</p>
          )}
        </div>
      )}
      <JobDialog jobId={jobId} onClose={() => setJobId(null)} />
    </div>
  );
}
