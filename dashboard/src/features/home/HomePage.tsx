import { AlertTriangle, ArrowRight, KeyRound, Loader2, Plus, Rocket } from 'lucide-react';
import type { CredentialHandoff, Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Dot } from '../../components/Status';
import { Link } from '../../lib/router';
import {
  isEverydayApp,
  isInstalled,
  isRunning,
  isWorking,
  launchTarget,
  needsAttention,
  stateLabel,
  stateTone,
} from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';
import { Agenda } from './Agenda';
import { GetStarted } from './GetStarted';
import { SystemStatus } from './SystemStatus';

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

function LauncherTile({ service }: { service: Service }) {
  const target = launchTarget(service);
  const tone = stateTone(service.state);
  const body = (
    <>
      <AppIcon id={service.id} size="lg" />
      <span className="tile-name">{service.name}</span>
      {!isRunning(service) && (
        <span className="tile-state">
          <Dot tone={tone} />
          {stateLabel[service.state]}
        </span>
      )}
    </>
  );
  if (target)
    return (
      <a className="tile" href={target.url} target="_blank" rel="noreferrer" aria-label={`Open ${service.name}`}>
        {body}
      </a>
    );
  return (
    <Link
      className="tile tile-muted"
      to={`/apps/${service.id}`}
      aria-label={`${service.name}: ${stateLabel[service.state]}`}
    >
      {body}
    </Link>
  );
}

function Attention() {
  const { data } = useDashboard();
  const operator = data.identity.writes_enabled;
  const handoffs = useApi<{ handoffs: CredentialHandoff[] }>(operator ? '/api/v1/credential-handoffs' : null, {
    interval: 60000,
  });
  const failing = data.services.services.filter(needsAttention);
  const saved = handoffs.data?.handoffs.length || 0;
  const working = data.services.services.filter(isWorking);
  const setupIncomplete = data.provisioning.available && !data.provisioning.complete;
  const items = [
    ...failing.map((service) => ({
      key: service.id,
      icon: AlertTriangle,
      tone: 'warning',
      text: `${service.name}: ${stateLabel[service.state].toLowerCase()}`,
      to: `/apps/${service.id}`,
    })),
    ...(saved
      ? [
          {
            key: 'handoffs',
            icon: KeyRound,
            tone: 'info',
            text: `${saved} new app password${saved === 1 ? '' : 's'} to save in Vaultwarden`,
            to: '/settings/security',
          },
        ]
      : []),
    ...(setupIncomplete
      ? [{ key: 'setup', icon: Rocket, tone: 'info', text: 'Finish setting up Mu3Lab', to: '/settings/system' }]
      : []),
    ...working.map((service) => ({
      key: `working-${service.id}`,
      icon: Loader2,
      tone: 'progress',
      text: `${service.name} is ${stateLabel[service.state].toLowerCase()}…`,
      to: `/apps/${service.id}`,
    })),
  ];
  if (!items.length) return null;
  return (
    <section className="attention" aria-label="Needs attention">
      {items.map(({ key, icon: Icon, tone, text, to }) => (
        <Link key={key} to={to} className={`attention-item attention-${tone}`}>
          <Icon className={tone === 'progress' ? 'spin' : ''} />
          <span>{text}</span>
          <ArrowRight className="attention-arrow" />
        </Link>
      ))}
    </section>
  );
}

export function HomePage() {
  const { data } = useDashboard();
  const services = data.services.services;
  const apps = services.filter((service) => isEverydayApp(service) && isInstalled(service));
  const name = (data.identity.display_name || data.identity.username || '').split(' ')[0];
  return (
    <div className="home">
      <header className="home-header">
        <h1>
          {greeting()}
          {name ? `, ${name}` : ''}
        </h1>
        <p>{new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</p>
      </header>
      <SystemStatus />
      <Attention />
      <GetStarted />
      <section aria-labelledby="apps-heading">
        <div className="section-title">
          <h2 id="apps-heading">Your apps</h2>
          <Link to="/apps">Manage</Link>
        </div>
        <div className="launcher">
          {apps.map((service) => (
            <LauncherTile key={service.id} service={service} />
          ))}
          <Link to="/apps/discover" className="tile tile-add" aria-label="Add apps">
            <span className="app-icon app-icon-lg">
              <Plus />
            </span>
            <span className="tile-name">Add apps</span>
          </Link>
        </div>
      </section>
      <Agenda nextcloud={services.find((service) => service.id === 'nextcloud')} />
    </div>
  );
}
