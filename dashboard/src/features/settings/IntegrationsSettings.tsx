import { ChevronDown, ChevronRight, MessageSquare, Plug, PlugZap } from 'lucide-react';
import { useState } from 'react';
import type { McpServer } from '../../api';
import type { ChatAssistant } from '../../api/models';
import { AppIcon } from '../../components/AppIcon';
import { Button, LinkButton } from '../../components/Button';
import { Card, Collapsible, EmptyState, PageHeader } from '../../components/Layout';
import { Badge, Dot } from '../../components/Status';
import { Link } from '../../lib/router';
import { isEverydayApp, isInstalled } from '../../lib/services';
import { useAction } from '../../lib/useAction';
import {
  mcpSummary,
  nextMcpStep,
  openChatFor,
  primaryConnectors,
  queueMcp,
  setsUpAutomatically,
  useMcpRegistry,
} from '../integrations/mcp';
import { useDashboard, useIsAdmin } from '../../state/dashboard';

function IntegrationRow({
  server,
  assistant,
  reload,
}: {
  server: McpServer;
  /** The app's assistant in chat, when the chat app reports one. */
  assistant?: ChatAssistant;
  reload: () => void;
}) {
  const { pending, run } = useAction();
  const { data } = useDashboard();
  const appName = data.services.services.find((service) => service.id === server.service_id)?.name || server.name;
  const summary = mcpSummary(server, appName);
  const next = nextMcpStep(server);
  // Preparing a stopped app's connection is an advanced step; keep it on the app's Chat tab.
  const step = next?.action === 'prepare' ? null : next;
  return (
    <div className="row">
      <Link to={`/apps/${server.service_id}/chat`} className="row-main">
        <AppIcon id={server.service_id} />
        <span className="row-text">
          {/* The app's own name; the connector's name ("… MCP") is a technical detail on its page. */}
          <b title={server.name}>{appName}</b>
          <small>{summary.detail}</small>
          {/* Why the app's assistant is not in chat yet; a ready one needs no extra line, and a stopped
              app's is explained once for the whole group. */}
          {assistant && assistant.status !== 'ready' && server.state !== 'stopped' && (
            <small>Assistant: {assistant.detail}</small>
          )}
        </span>
      </Link>
      <Badge tone={summary.tone}>
        <Dot tone={summary.tone} />
        {summary.label}
      </Badge>
      <span className="row-actions">
        {step?.action === 'chat' ? (
          <Button size="sm" icon={MessageSquare} onClick={() => openChatFor(server)}>
            Chat
          </Button>
        ) : step ? (
          <Button
            size="sm"
            icon={PlugZap}
            loading={pending === step.action}
            onClick={async () => {
              await run(step.action, () => queueMcp(server, step.action as 'install' | 'prepare'), 'Connecting…');
              window.setTimeout(reload, 1200);
            }}
          >
            {step.action === 'install' ? 'Connect' : step.label}
          </Button>
        ) : !setsUpAutomatically(server) && server.state === 'authentication_required' ? (
          <LinkButton size="sm" to={`/apps/${server.service_id}/chat`}>
            Add credential
          </LinkButton>
        ) : (
          <span className="row-action-placeholder" />
        )}
        <Link
          to={`/apps/${server.service_id}/chat`}
          className="btn btn-ghost btn-sm btn-icon"
          aria-label={`Manage ${appName}`}
        >
          <ChevronRight />
        </Link>
      </span>
    </div>
  );
}

/** Stopped apps' integrations in one folded row: they all wait for the same thing. */
function StoppedRows({ servers, row }: { servers: McpServer[]; row: (server: McpServer) => JSX.Element }) {
  const [open, setOpen] = useState(false);
  if (!servers.length) return null;
  if (open) return <>{servers.map(row)}</>;
  return (
    <button type="button" className="row row-more" aria-expanded={false} onClick={() => setOpen(true)}>
      <ChevronDown />
      <span className="row-more-text">
        {servers.length} {servers.length === 1 ? 'app is' : 'apps are'} stopped
        <small>Their integrations and assistants come back when each app starts.</small>
      </span>
      <span className="row-more-icons" aria-hidden="true">
        {servers.slice(0, 8).map((server) => (
          <AppIcon key={server.id} id={server.service_id} size="sm" />
        ))}
      </span>
    </button>
  );
}

/** Installed apps Mu3Lab has no chat integration for yet, so every app is accounted for. */
function WithoutIntegration({ servers }: { servers: McpServer[] }) {
  const { data } = useDashboard();
  const covered = new Set(servers.map((server) => server.service_id));
  const apps = data.services.services.filter(
    (service) => isEverydayApp(service) && isInstalled(service) && !covered.has(service.id),
  );
  if (!apps.length) return null;
  return (
    <Card
      title="No integration yet"
      description="Mu3Lab doesn’t offer a reviewed chat integration for these apps yet. Each one is added only after its code is checked."
      flush
    >
      <section className="rows" aria-label="Apps without an integration">
        {apps.map((service) => (
          <div className="row" key={service.id}>
            <Link to={`/apps/${service.id}`} className="row-main">
              <AppIcon id={service.id} />
              <span className="row-text">
                <b>{service.name}</b>
                <small>Not available in chat yet</small>
              </span>
            </Link>
          </div>
        ))}
      </section>
    </Card>
  );
}

export function IntegrationsSettings() {
  const registry = useMcpRegistry();
  const servers = primaryConnectors(registry.data?.servers || []);
  const automatic = servers.filter(setsUpAutomatically);
  const manual = servers.filter((server) => !setsUpAutomatically(server));
  const live = servers.filter((server) => server.state === 'live').length;
  // Connected first, then the ones to act on; stopped apps fold into one row at the end.
  const rank = (server: McpServer) => (server.state === 'live' ? 0 : 1);
  const awake = automatic.filter((server) => server.state !== 'stopped').sort((a, b) => rank(a) - rank(b));
  const asleep = automatic.filter((server) => server.state === 'stopped');
  const reload = () => void registry.reload();
  const { data } = useDashboard();
  const isAdmin = useIsAdmin();
  const assistants = data.chat.assistants || [];
  const assistantFor = (server: McpServer) => assistants.find((item) => item.id === server.service_id);
  const ready = assistants.filter((item) => item.status === 'ready').length;
  const row = (server: McpServer) => (
    <IntegrationRow key={server.id} server={server} assistant={assistantFor(server)} reload={reload} />
  );
  return (
    <>
      <PageHeader
        title="Chat integrations"
        description="Connect supported apps to chat. ContextForge chooses which of their tools chat can use."
      />
      {registry.data && servers.length > 0 && (
        <div className="stat-row" aria-label="Integration summary">
          <span>
            <b>{live}</b> connected
          </span>
          {assistants.length > 0 && (
            <span>
              <b>
                {ready} of {assistants.length}
              </b>{' '}
              assistants in chat
            </span>
          )}
          {asleep.length > 0 && (
            <span>
              <b>{asleep.length}</b> waiting for their app
            </span>
          )}
          {manual.length > 0 && (
            <span>
              <b>{manual.length}</b> need a credential
            </span>
          )}
        </div>
      )}
      {!registry.data ? (
        <p className="muted">{registry.error || 'Loading integrations…'}</p>
      ) : !servers.length ? (
        <>
          <WithoutIntegration servers={servers} />
          <EmptyState
            icon={Plug}
            title="No integrations yet"
            action={<LinkButton to="/apps/discover">Discover apps</LinkButton>}
          >
            Integrations appear here for installed apps that support chat.
          </EmptyState>
        </>
      ) : (
        <>
          <Card
            title="Set up automatically"
            description="Mu3Lab creates the credentials when you connect. Each app's assistant is the same for everyone; ContextForge chooses its tools and logs every call."
            actions={
              isAdmin && (
                <LinkButton size="sm" to="/apps/contextforge">
                  Manage tools
                </LinkButton>
              )
            }
            flush
          >
            <section className="rows" aria-label="Set up automatically">
              {automatic.length ? (
                <>
                  {awake.map(row)}
                  <StoppedRows servers={asleep} row={row} />
                </>
              ) : (
                <p className="muted card-pad">None yet.</p>
              )}
            </section>
          </Card>
          {manual.length > 0 && (
            <Card
              title="Needs your credential"
              description="Create a credential in the app, then add it to Mu3Lab to connect the integration."
              flush
            >
              <section className="rows" aria-label="Manual integration">
                {manual.map(row)}
              </section>
            </Card>
          )}
          <WithoutIntegration servers={servers} />
          <Collapsible title="How integrations are secured">
            <ul className="bullets">
              <li>Credentials are scoped to one app, stored encrypted, and never shown again.</li>
              <li>Tools become available only after the connection is verified.</li>
              <li>Chat reaches apps only through ContextForge, which checks who is asking and logs every call.</li>
              <li>Tools that change data run without asking; switch off any you don't want in ContextForge.</li>
              <li>Vaultwarden and infrastructure services are never exposed to chat.</li>
            </ul>
            {registry.data.policy && <p className="muted">{registry.data.policy}</p>}
          </Collapsible>
        </>
      )}
    </>
  );
}
