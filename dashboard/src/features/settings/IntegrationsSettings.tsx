import { ChevronRight, MessageSquare, Plug, PlugZap } from 'lucide-react';
import type { McpServer } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button, LinkButton } from '../../components/Button';
import { Card, Collapsible, EmptyState, PageHeader } from '../../components/Layout';
import { Badge, Dot } from '../../components/Status';
import { Link } from '../../lib/router';
import { useAction } from '../../lib/useAction';
import {
  mcpSummary,
  nextMcpStep,
  openChatFor,
  queueMcp,
  setsUpAutomatically,
  useMcpRegistry,
} from '../integrations/mcp';
import { useDashboard } from '../../state/dashboard';

function IntegrationRow({ server, reload }: { server: McpServer; reload: () => void }) {
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
          <b>{server.name}</b>
          <small>{summary.detail}</small>
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
          aria-label={`Manage ${server.name}`}
        >
          <ChevronRight />
        </Link>
      </span>
    </div>
  );
}

export function IntegrationsSettings() {
  const registry = useMcpRegistry();
  const servers = registry.data?.servers || [];
  const automatic = servers.filter(setsUpAutomatically);
  const manual = servers.filter((server) => !setsUpAutomatically(server));
  const live = servers.filter((server) => server.state === 'live').length;
  const reload = () => void registry.reload();
  return (
    <>
      <PageHeader
        title="Chat integrations"
        description="Let chat read and act on data in your apps. Each app only exposes the tools you allow."
      />
      {registry.data && servers.length > 0 && (
        <div className="stat-row" aria-label="Integration summary">
          <span>
            <b>{live}</b> connected
          </span>
          <span>
            <b>{automatic.length}</b> set up automatically
          </span>
          <span>
            <b>{manual.length}</b> need a credential
          </span>
        </div>
      )}
      {!registry.data ? (
        <p className="muted">{registry.error || 'Loading integrations…'}</p>
      ) : !servers.length ? (
        <EmptyState
          icon={Plug}
          title="No integrations yet"
          action={<LinkButton to="/apps/discover">Discover apps</LinkButton>}
        >
          Integrations appear here for installed apps that support chat.
        </EmptyState>
      ) : (
        <>
          <Card title="Set up automatically" description="Mu3Lab creates credentials and keeps these connected." flush>
            <section className="rows" aria-label="Set up automatically">
              {automatic.length ? (
                automatic.map((server) => <IntegrationRow key={server.id} server={server} reload={reload} />)
              ) : (
                <p className="muted card-pad">None yet.</p>
              )}
            </section>
          </Card>
          {manual.length > 0 && (
            <Card
              title="Needs your credential"
              description="These apps require a key or password only you can create."
              flush
            >
              <section className="rows" aria-label="Manual integration">
                {manual.map((server) => (
                  <IntegrationRow key={server.id} server={server} reload={reload} />
                ))}
              </section>
            </Card>
          )}
          <Collapsible title="How integrations are secured">
            <ul className="bullets">
              <li>Credentials are scoped to one app, stored encrypted, and never shown again.</li>
              <li>Tools become available only after the connection is verified.</li>
              <li>Actions that change data ask for your approval by default.</li>
              <li>Vaultwarden and infrastructure services are never exposed to chat.</li>
            </ul>
            {registry.data.policy && <p className="muted">{registry.data.policy}</p>}
          </Collapsible>
        </>
      )}
    </>
  );
}
