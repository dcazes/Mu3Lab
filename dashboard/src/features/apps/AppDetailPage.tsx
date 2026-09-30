import { AlertTriangle, KeyRound, Plug, UserPlus, Wrench } from 'lucide-react';
import type { Service } from '../../api';
import { postApi } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button, ExternalButton, LinkButton } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card, Facts, PageHeader, Tabs } from '../../components/Layout';
import { Menu } from '../../components/Menu';
import { Badge, Dot, StatusBadge } from '../../components/Status';
import { CopyField } from '../../components/CopyField';
import { companionFor } from '../../lib/companions';
import { humanize, relativeTime } from '../../lib/format';
import { Link } from '../../lib/router';
import { isInstalled, isRunning, launchTarget, signInSummary } from '../../lib/services';
import { useAction } from '../../lib/useAction';
import { useDashboard } from '../../state/dashboard';
import { McpPanel } from '../integrations/McpPanel';
import { useMcpRegistry } from '../integrations/mcp';
import { AdvancedTab } from './AdvancedTab';
import { ConfigurationForm } from './ConfigurationForm';
import { DevicesSection } from './DevicesSection';
import { ACTIONS, type ServiceAction, useServiceActions } from './useServiceActions';

function HeaderActions({ service }: { service: Service }) {
  const { pending, perform, actions } = useServiceActions(service);
  const target = launchTarget(service);
  const primaryAction = (['start', 'install', 'retry_setup'] as ServiceAction[]).find((action) =>
    actions.includes(action),
  );
  const menuActions = actions.filter((action) => action !== primaryAction || target);
  return (
    <>
      {service.id === 'freellmapi' && <LinkButton to="/settings/ai">Manage providers</LinkButton>}
      {target ? (
        <ExternalButton variant="primary" href={target.url}>
          {target.label}
        </ExternalButton>
      ) : (
        primaryAction && (
          <Button
            variant="primary"
            icon={ACTIONS[primaryAction].icon}
            loading={pending === primaryAction}
            onClick={() => void perform(primaryAction)}
          >
            {ACTIONS[primaryAction].label}
          </Button>
        )
      )}
      <Menu
        label={`${service.name} actions`}
        items={menuActions
          .filter((action) => !(target && action === 'start'))
          .map((action) => ({
            label: ACTIONS[action].label,
            icon: ACTIONS[action].icon,
            danger: ACTIONS[action].danger,
            disabled: Boolean(pending),
            onSelect: () => void perform(action),
          }))}
      />
    </>
  );
}

function NextSteps({ service }: { service: Service }) {
  const confirm = useConfirm();
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const target = launchTarget(service);
  const identity = service.identity;
  const steps = [];

  if (service.blocked_reason && service.stage === 'blocked')
    steps.push(
      <Callout key="blocked" tone="info" title="Not available yet">
        {service.blocked_reason}
      </Callout>,
    );

  if (['failed', 'needs_attention', 'degraded'].includes(service.state) && isInstalled(service))
    steps.push(
      <Callout
        key="failed"
        tone="danger"
        icon={AlertTriangle}
        title={`${service.name} needs attention`}
        action={<LinkButton to={`/apps/${service.id}/advanced`}>View logs</LinkButton>}
      >
        {(service.last_job?.state === 'failed' && service.last_job.detail) || service.detail}
      </Callout>,
    );

  if (service.state === 'config_required')
    steps.push(
      <Callout
        key="config"
        tone="warning"
        title="Finish configuration"
        action={<LinkButton to={`/apps/${service.id}/settings`}>Open settings</LinkButton>}
      >
        {service.name} needs a few settings before it can be installed.
      </Callout>,
    );

  if (service.initialization?.state === 'awaiting_user')
    steps.push(
      <Callout
        key="account"
        tone="info"
        icon={UserPlus}
        title="Complete account setup"
        action={
          <Button
            loading={pending === 'init'}
            onClick={async () => {
              if (
                !(await confirm({
                  title: `Have you completed account setup for ${service.name}?`,
                  confirmLabel: 'Confirm setup',
                }))
              )
                return;
              await run(
                'init',
                () => postApi(`/api/v1/services/${service.id}/initialization/confirm`),
                'Setup confirmed',
              );
              void refresh();
            }}
          >
            Confirm setup complete
          </Button>
        }
      >
        {service.account?.user_action || `Open ${service.name} and create the first account.`}
      </Callout>,
    );

  if (identity?.state === 'migration_required')
    steps.push(
      <Callout
        key="sso"
        tone="info"
        icon={KeyRound}
        title="Finish single sign-on"
        action={target && <ExternalButton href={target.url}>Open {service.name}</ExternalButton>}
      >
        Sign in to {service.name} with Authentik to link your account. Mu3Lab must verify account ownership and
        administrator access before disabling local browser sign-in.
      </Callout>,
    );
  else if (signInSummary(service).repairable && identity?.state !== 'configuring' && isRunning(service))
    steps.push(
      <Callout
        key="repair"
        tone="warning"
        icon={Wrench}
        title="Sign-in configuration needs attention"
        action={
          <Button
            loading={pending === 'repair'}
            onClick={async () => {
              await run(
                'repair',
                () => postApi(`/api/v1/services/${service.id}/identity/reconcile`),
                'Sign-in repair started',
              );
              void refresh();
            }}
          >
            Repair sign-in
          </Button>
        }
      >
        {identity?.detail || `Mu3Lab can connect ${service.name} to Authentik for you.`}
      </Callout>,
    );

  return steps.length ? <div className="stack">{steps}</div> : null;
}

function Overview({ service, address, devices }: { service: Service; address: string; devices: boolean }) {
  const { data } = useDashboard();
  const info = data.catalog.services[service.id];
  const signIn = signInSummary(service);
  const summary = info?.summary || service.detail;
  const note = service.identity_note && service.identity_note !== summary ? service.identity_note : '';
  const dependencies = service.dependencies
    .map((id) => data.services.services.find((item) => item.id === id))
    .filter((item): item is Service => Boolean(item));
  return (
    <div className="stack">
      <NextSteps service={service} />
      <Card title="About">
        <p className="lead">{summary}</p>
        {note && <p className="muted">{note}</p>}
        <Facts
          items={[
            { label: 'Status', value: <StatusBadge state={service.state} /> },
            {
              label: 'Sign-in',
              value: (
                <span className="inline">
                  <Dot tone={signIn.tone} />
                  {signIn.label}
                  {service.identity &&
                    service.identity.state !== 'ready' &&
                    service.identity.state !== 'unsupported' && (
                      <span className="muted">· {humanize(service.identity.state)}</span>
                    )}
                </span>
              ),
            },
            // With a devices section below, its server address card carries the address.
            ...(address && !devices
              ? [{ label: 'Web address', value: <CopyField value={address} label="Web address" /> }]
              : []),
            ...(dependencies.length
              ? [
                  {
                    label: 'Uses',
                    value: (
                      <span className="inline wrap">
                        {dependencies.map((item) => (
                          <Link key={item.id} to={`/apps/${item.id}`} className="chip">
                            <AppIcon id={item.id} size="sm" />
                            {item.name}
                          </Link>
                        ))}
                      </span>
                    ),
                  },
                ]
              : []),
            ...(service.last_job
              ? [
                  {
                    label: 'Last activity',
                    value: `${humanize(service.last_job.action)} ${service.last_job.state} ${relativeTime(service.last_job.updated_at || service.last_job.created_at)}`,
                  },
                ]
              : []),
            ...(info?.resource_guidance ? [{ label: 'Resources', value: info.resource_guidance }] : []),
          ]}
        />
      </Card>
      {devices && <DevicesSection service={service} address={address} />}
    </div>
  );
}

export function AppDetailPage({ id, tab }: { id: string; tab: string }) {
  const { data } = useDashboard();
  const service = data.services.services.find((item) => item.id === id)!;
  const registry = useMcpRegistry();
  const mcp = registry.data?.servers.find((server) => server.service_id === id);
  const installed = isInstalled(service);
  const address = service.ui?.state === 'ready' && service.ui.url ? service.ui.url : '';
  const hasDevices =
    installed &&
    Boolean(address) &&
    (Boolean(companionFor(id)) || service.stage === 'optional' || id === 'vaultwarden');
  const hasSettings = Boolean(service.configuration?.length);
  const base = `/apps/${id}`;
  const tabs = [
    { label: 'Overview', to: base, key: 'overview' },
    ...(mcp ? [{ label: 'Chat', to: `${base}/chat`, key: 'chat' }] : []),
    ...(hasSettings ? [{ label: 'Settings', to: `${base}/settings`, key: 'settings' }] : []),
    ...(installed ? [{ label: 'Advanced', to: `${base}/advanced`, key: 'advanced' }] : []),
  ];
  const active = tabs.some((item) => item.key === tab) ? tab : 'overview';

  return (
    <div className="page">
      <PageHeader
        breadcrumb={{ label: 'Apps', to: installed ? '/apps' : '/apps/discover' }}
        title={
          <span className="title-with-icon">
            <AppIcon id={service.id} size="xl" />
            <span>
              {service.name}
              <small>
                <StatusBadge state={service.state} />
                {mcp?.state === 'live' && (
                  <Badge tone="gray">
                    <Plug /> In chat
                  </Badge>
                )}
              </small>
            </span>
          </span>
        }
        actions={<HeaderActions service={service} />}
      />
      {tabs.length > 1 && <Tabs label={`${service.name} sections`} items={tabs} />}
      {active === 'overview' && <Overview service={service} address={address} devices={hasDevices} />}
      {active === 'chat' && mcp && (
        <McpPanel server={mcp} appName={service.name} reload={() => void registry.reload()} />
      )}
      {active === 'settings' && <ConfigurationForm service={service} />}
      {active === 'advanced' && <AdvancedTab service={service} />}
    </div>
  );
}
