import { CheckCircle2, MessageSquare, PlugZap, RefreshCw, ShieldCheck, Unplug } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { api, type McpServer, putJsonApi } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Card, Collapsible } from '../../components/Layout';
import { Badge, Dot } from '../../components/Status';
import { useAction } from '../../lib/useAction';
import { mcpSummary, nextMcpStep, openChatFor, queueMcp, setsUpAutomatically } from './mcp';
import { ToolConsole } from './ToolConsole';

const PERMISSION_LABEL: Record<string, string> = {
  auto: 'Allowed',
  needs_approval: 'Ask first',
  disabled: 'Off',
};

function Tools({ server }: { server: McpServer }) {
  const { run } = useAction();
  const [permissions, setPermissions] = useState<Record<string, string>>({});
  if (!server.tools.length) return <p className="muted">Tools appear here once the connection is verified.</p>;
  return (
    <div className="rows">
      {server.tools.map((tool) => {
        const value = permissions[tool.id] || tool.permission || (tool.risk === 'read' ? 'auto' : 'needs_approval');
        return (
          <div className="row" key={tool.id}>
            <span className="row-text">
              <b>{tool.title || tool.id}</b>
              <small>{tool.risk === 'read' ? 'Reads data' : 'Changes data'}</small>
            </span>
            {server.state === 'live' ? (
              <select
                className="select-sm"
                aria-label={`${tool.title} permission`}
                value={value}
                onChange={async (event) => {
                  const next = event.target.value;
                  const saved = await run(
                    tool.id,
                    () =>
                      putJsonApi(`/api/v1/mcp/servers/${server.id}/tools/${encodeURIComponent(tool.id)}/permission`, {
                        permission: next,
                      }),
                    `${tool.title}: ${PERMISSION_LABEL[next]}`,
                  );
                  if (saved) setPermissions((current) => ({ ...current, [tool.id]: next }));
                }}
              >
                {tool.risk === 'read' && <option value="auto">Allowed</option>}
                <option value="needs_approval">Ask first</option>
                <option value="disabled">Off</option>
              </select>
            ) : (
              <Badge>{PERMISSION_LABEL[value]}</Badge>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function McpPanel({ server, appName, reload }: { server: McpServer; appName: string; reload: () => void }) {
  const confirm = useConfirm();
  const summary = mcpSummary(server, appName);
  const { pending, run } = useAction();
  const [values, setValues] = useState<Record<string, string>>({});
  const [logs, setLogs] = useState<string[] | null>(null);
  const step = nextMcpStep(server);
  const manual = !setsUpAutomatically(server);
  const help =
    server.auth.auto_provision_note ||
    (server.service_id === 'surfsense'
      ? 'In SurfSense, create a personal token under API Playground → API Keys, enable its workspace, then paste the ss_pat_… token here.'
      : '');

  const act = async (action: 'prepare' | 'install' | 'restart' | 'disable' | 'verify') => {
    if (
      ['restart', 'disable'].includes(action) &&
      !(await confirm({
        title: `${action === 'disable' ? 'Disconnect' : 'Restart'} ${server.name}?`,
        description: action === 'disable' ? 'Chat loses access to this app until you connect it again.' : undefined,
        confirmLabel: action === 'disable' ? 'Disconnect' : 'Restart',
        tone: action === 'disable' ? 'danger' : 'primary',
      }))
    )
      return;
    await run(action, () => queueMcp(server, action), 'Working on it — this takes a moment.');
    window.setTimeout(reload, 1200);
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const saved = await run(
      'save',
      () => putJsonApi(`/api/v1/mcp/servers/${server.id}/configuration`, { values }),
      'Credential saved privately',
    );
    if (saved) {
      setValues({});
      reload();
    }
  };
  const loadLogs = () =>
    run('logs', async () => {
      const result = await api<{ lines: string[] }>(`/api/v1/mcp/servers/${server.id}/logs?tail=120`);
      setLogs(result.lines);
    });

  return (
    <div className="stack">
      <Card
        title={
          <>
            <Dot tone={summary.tone} /> {summary.label}
          </>
        }
        description={summary.detail}
        actions={
          step &&
          (step.action === 'chat' ? (
            <Button variant="primary" icon={MessageSquare} onClick={() => openChatFor(server)}>
              Open chat
            </Button>
          ) : (
            <Button
              variant="primary"
              icon={PlugZap}
              loading={pending === step.action}
              onClick={() => void act(step.action as 'prepare' | 'install')}
            >
              {step.action === 'prepare' ? 'Prepare connection' : step.label}
            </Button>
          ))
        }
      >
        {help && <p className="muted">{help}</p>}
        {manual && server.configuration && server.configuration.length > 0 && (
          <form className="form" onSubmit={save}>
            {server.configuration.map((field) => (
              <label className="field" key={field.key}>
                <span>{field.label || field.key}</span>
                <input
                  type={field.type === 'secret' ? 'password' : 'text'}
                  autoComplete="off"
                  required={field.required && !field.secret_present}
                  value={values[field.key] || ''}
                  placeholder={field.secret_present ? 'Saved — leave blank to keep' : ''}
                  onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))}
                />
              </label>
            ))}
            <div>
              <Button type="submit" loading={pending === 'save'}>
                Save credential
              </Button>
            </div>
          </form>
        )}
      </Card>
      <Card title="What chat can do" description="Choose which actions need your approval in chat.">
        <Tools server={server} />
      </Card>
      <Collapsible title="Advanced">
        <div className="stack">
          <div className="button-row">
            {server.state === 'live' && (
              <>
                <Button icon={CheckCircle2} loading={pending === 'verify'} onClick={() => void act('verify')}>
                  Verify
                </Button>
                <Button icon={RefreshCw} loading={pending === 'restart'} onClick={() => void act('restart')}>
                  Restart
                </Button>
                <Button icon={Unplug} className="danger-text" onClick={() => void act('disable')}>
                  Disconnect
                </Button>
              </>
            )}
            {server.enabled && (
              <Button loading={pending === 'logs'} onClick={() => void loadLogs()}>
                View logs
              </Button>
            )}
            {server.review?.repository && (
              <ExternalButton icon={ShieldCheck} href={server.review.repository}>
                Reviewed source
              </ExternalButton>
            )}
          </div>
          <p className="muted">
            {server.kind} · {server.transport}
            {server.last_verified_at && ` · verified ${new Date(server.last_verified_at).toLocaleString()}`}
            {server.review?.note && ` · ${server.review.note}`}
          </p>
          {logs && <pre className="log">{logs.join('\n') || 'No recent log lines.'}</pre>}
          <ToolConsole server={server} />
        </div>
      </Collapsible>
    </div>
  );
}
