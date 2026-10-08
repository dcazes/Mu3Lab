import { AlertTriangle, KeyRound, Plug, UserPlus } from 'lucide-react';
import { useState } from 'react';
import type { Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button, ExternalButton, LinkButton } from '../../components/Button';
import { Callout, Card, Facts, PageHeader, Tabs } from '../../components/Layout';
import { Menu } from '../../components/Menu';
import { Badge, Dot, StatusBadge } from '../../components/Status';
import { CopyField } from '../../components/CopyField';
import { humanize, relativeTime } from '../../lib/format';
import { Link } from '../../lib/router';
import { isInstalled, isRunning, launchTarget, requiredChecks, signInSummary } from '../../lib/services';
import { useDashboard, useIsAdmin } from '../../state/dashboard';
import { McpPanel } from '../integrations/McpPanel';
import { connectorsFor, useMcpRegistry } from '../integrations/mcp';
import { AdvancedTab } from './AdvancedTab';
import { ConfigurationForm } from './ConfigurationForm';
import { DevicesSection } from './DevicesSection';
import { UninstallDialog } from './UninstallDialog';
import { ACTIONS, type ServiceAction, useServiceActions } from './useServiceActions';

function HeaderActions({ service }: { service: Service }) {
  const { pending, perform, actions } = useServiceActions(service);
  const [uninstalling, setUninstalling] = useState(false);
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
          .filter((action) => !(target && action === 'start') && action !== 'uninstall_delete_data')
          .map((action) => ({
            label: ACTIONS[action].label,
            icon: ACTIONS[action].icon,
            danger: ACTIONS[action].danger,
            disabled: Boolean(pending),
            onSelect: () => (action === 'uninstall' ? setUninstalling(true) : void perform(action)),
          }))}
      />
      <UninstallDialog service={service} open={uninstalling} onClose={() => setUninstalling(false)} />
    </>
  );
}

function NextSteps({ service }: { service: Service }) {
  const target = launchTarget(service);
  const identity = service.identity;
  const steps = [];

  if (['failed', 'needs_attention', 'degraded'].includes(service.display_state) && isInstalled(service))
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

  if (
    service.display_state === 'needs_attention' &&
    !service.installed &&
    (service.configuration || []).some((field) => field.required && !field.value && !field.secret_present)
  )
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
        title="Ready for your first visit"
        action={target && <ExternalButton href={target.url}>Open {service.name}</ExternalButton>}
      >
        {service.account?.user_action ||
          `Open ${service.name} to finish signing in. Mu3Lab checks setup automatically.`}
      </Callout>,
    );

  if (identity?.state === 'degraded' && isRunning(service))
    steps.push(
      <Callout key="sign-in" tone="warning" icon={KeyRound} title="Sign-in is not working">
        {identity.detail || `${service.name} could not confirm its Authentik sign-in.`}
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
            { label: 'Status', value: <StatusBadge state={service.display_state} /> },
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
            ...(isInstalled(service) && requiredChecks(service).length
              ? [{ label: 'Checks', value: <Checks service={service} /> }]
              : []),
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

function Checks({ service }: { service: Service }) {
  return (
    <ul className="check-list">
      {requiredChecks(service).map((check) => (
        <li key={check.name} className="inline wrap">
          <Dot tone={check.tone} />
          {check.label}
          <span className="muted">
            · {humanize(check.state === 'pass' ? 'OK' : check.state)}
            {check.state !== 'pass' && check.last_success_at && `, last OK ${relativeTime(check.last_success_at)}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function AppDetailPage({ id, tab }: { id: string; tab: string }) {
  const { data } = useDashboard();
  const isAdmin = useIsAdmin();
  const service = data.services.services.find((item) => item.id === id)!;
  const registry = useMcpRegistry();
  const connectors = connectorsFor(registry.data?.servers || [], id);
  const mcp = connectors[0];
  const installed = isInstalled(service);
  const address = service.ui?.state === 'ready' && service.ui.url ? service.ui.url : '';
  const hasDevices =
    installed &&
    Boolean(address) &&
    (Boolean(service.mobile && 'clients' in service.mobile) || service.stage === 'optional' || id === 'vaultwarden');
  const hasSettings = Boolean(service.configuration?.length);
  const base = `/apps/${id}`;
  const tabs = [
    { label: 'Overview', to: base, key: 'overview' },
    ...(mcp ? [{ label: 'Chat', to: `${base}/chat`, key: 'chat' }] : []),
    ...(hasSettings ? [{ label: 'Settings', to: `${base}/settings`, key: 'settings' }] : []),
    ...(installed && isAdmin ? [{ label: 'Advanced', to: `${base}/advanced`, key: 'advanced' }] : []),
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
                <StatusBadge state={service.display_state} />
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
        <McpPanel server={mcp} connectors={connectors} appName={service.name} reload={() => void registry.reload()} />
      )}
      {active === 'settings' && <ConfigurationForm service={service} />}
      {active === 'advanced' && <AdvancedTab service={service} />}
    </div>
  );
}
