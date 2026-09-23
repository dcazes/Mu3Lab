import { useState } from 'react';
import { api, type Job, type JobDetailResponse, type McpServer, postApi, postJsonApi, putJsonApi } from './api';

interface Call { id: number; server_id: string; tool_name: string; source: string; actor: string; outcome: string; duration_ms: number; created_at: string; detail: string }

export function McpConsole({ server }: { server: McpServer }) {
  const [selected, setSelected] = useState('');
  const [input, setInput] = useState('{}');
  const [result, setResult] = useState('');
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [calls, setCalls] = useState<Call[] | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [jobDetail, setJobDetail] = useState<JobDetailResponse | null>(null);
  const [permissions, setPermissions] = useState<Record<string, string>>({});
  const [updateInfo, setUpdateInfo] = useState<{ current_version: string; reviewed_update: { version: string; release_url: string; notes?: string } | null } | null>(null);
  const [updateMessage, setUpdateMessage] = useState('');
  const tool = server.tools.find(item => item.id === selected);
  const permission = tool ? permissions[tool.id] || tool.permission || (tool.risk === 'read' ? 'auto' : 'needs_approval') : '';

  const run = async () => {
    if (!tool) return;
    setError(''); setResult(''); setWorking(true);
    try {
      const args = JSON.parse(input) as unknown;
      const path = `/api/v1/mcp/servers/${server.id}/tools/${encodeURIComponent(tool.id)}`;
      const prepared = await postJsonApi<{ confirmation_required: boolean; confirmation_token: string }>(`${path}/prepare`, { arguments: args });
      if (prepared.confirmation_required && !window.confirm(`Run ${tool.title} in ${server.name}?\n\nInput:\n${input}`)) return;
      const response = await postJsonApi<{ ok: boolean; result: unknown; outcome: string }>(`${path}/execute`, { arguments: args, confirmation_token: prepared.confirmation_token });
      setResult(JSON.stringify(response.result, null, 2));
      if (!response.ok) setError(`Tool returned ${response.outcome}.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setWorking(false); }
  };

  const setPermission = async (name: string, value: string) => {
    setError('');
    try {
      await putJsonApi(`/api/v1/mcp/servers/${server.id}/tools/${encodeURIComponent(name)}/permission`, { permission: value });
      setPermissions(current => ({ ...current, [name]: value }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const loadActivity = async () => {
    setError('');
    try { setCalls((await api<{ calls: Call[] }>(`/api/v1/mcp/servers/${server.id}/activity`)).calls); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const loadJobs = async () => {
    setError('');
    try { setJobs((await api<{ jobs: Job[] }>(`/api/v1/mcp/servers/${server.id}/jobs`)).jobs); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const openJob = async (id: string) => {
    setError('');
    try { setJobDetail(await api<JobDetailResponse>(`/api/v1/jobs/${id}`)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const checkUpdates = async () => {
    setError('');
    try { setUpdateInfo(await api<{ current_version: string; reviewed_update: { version: string; release_url: string; notes?: string } | null }>(`/api/v1/mcp/servers/${server.id}/updates`)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const update = async () => {
    if (!updateInfo?.reviewed_update || !window.confirm(`Update ${server.name} to reviewed version ${updateInfo.reviewed_update.version}? The previous runtime will be restored if verification fails.`)) return;
    setError('');
    try { await postApi(`/api/v1/mcp/servers/${server.id}/update`); setUpdateMessage('Reviewed update queued. View connection jobs for progress.'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };

  return <div className="mcp-console">
    <h4>Tools</h4>
    <p>Only discovered tools can run. Writes require confirmation. Inputs and results are not saved in activity history.</p>
    {server.tools.length ? <div className="mcp-tool-rows">{server.tools.map(item => <div key={item.id}>
      <span><b>{item.title}</b><small>{item.id} · {item.risk}</small></span>
      {server.state === 'live' && <select aria-label={`${item.title} permission`} value={permissions[item.id] || item.permission || (item.risk === 'read' ? 'auto' : 'needs_approval')} onChange={event => void setPermission(item.id, event.target.value)}>{item.risk === 'read' && <option value="auto">Automatic</option>}<option value="needs_approval">Approval required</option><option value="disabled">Disabled</option></select>}
      {server.state === 'live' && <button className="button button-secondary" onClick={() => { setSelected(item.id); setInput('{}'); setResult(''); }}>Run</button>}
    </div>)}</div> : <p>No live tool list yet. Connect and verify this MCP first.</p>}
    {tool && server.state === 'live' && <section className="mcp-run-panel"><h4>Run {tool.title}</h4><p>{tool.risk} · {permission}</p><details><summary>Input schema</summary><pre>{JSON.stringify(tool.parameters || {}, null, 2)}</pre></details><label>Tool input as JSON<textarea spellCheck={false} value={input} onChange={event => setInput(event.target.value)} /></label><button className="button button-primary" disabled={working || permission === 'disabled'} onClick={() => void run()}>{working ? 'Running…' : 'Validate and run'}</button>{result && <pre aria-label="Tool result">{result}</pre>}</section>}
    <div className="actions"><button className="button button-secondary" onClick={() => void loadActivity()}>View action history</button><button className="button button-secondary" onClick={() => void loadJobs()}>View connection jobs</button><button className="button button-secondary" onClick={() => void checkUpdates()}>Check reviewed updates</button></div>
    {updateInfo && <section className="mcp-updates"><h4>Updates</h4><p>Current reviewed version: {updateInfo.current_version}</p>{updateInfo.reviewed_update ? <><p>Available: {updateInfo.reviewed_update.version}</p>{updateInfo.reviewed_update.notes && <p>{updateInfo.reviewed_update.notes}</p>}<a href={updateInfo.reviewed_update.release_url} target="_blank" rel="noreferrer">Release details ↗</a> <button className="button button-primary" disabled={server.state !== 'live'} onClick={() => void update()}>Update and verify</button></> : <p>No newer reviewed version is available.</p>}{updateMessage && <p role="status">{updateMessage}</p>}</section>}
    {error && <p className="error" role="alert">{error}</p>}
    {calls && <div className="mcp-history"><h4>Action history</h4>{calls.length ? calls.map(call => <div key={call.id}><time>{new Date(call.created_at).toLocaleString()}</time><b>{call.tool_name}</b><span>{call.source} · {call.outcome} · {call.duration_ms} ms</span></div>) : <p>No recorded calls.</p>}</div>}
    {jobs && <div className="mcp-history"><h4>Connection jobs</h4>{jobs.length ? jobs.map(job => <div key={job.id}><time>{new Date(job.created_at).toLocaleString()}</time><button className="button button-quiet" onClick={() => void openJob(job.id)}>{job.action}</button><span>{job.state} · {job.detail}</span></div>) : <p>No recorded jobs.</p>}{jobDetail && <section><h4>{jobDetail.job.action} · {jobDetail.job.state}</h4><p>{jobDetail.job.detail}</p><pre>{jobDetail.events.map(event => `${event.created_at} ${event.event}: ${event.detail}`).join('\n')}</pre></section>}</div>}
  </div>;
}
