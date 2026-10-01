import { KeyRound } from 'lucide-react';
import type { Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { ExternalButton } from '../../components/Button';
import { Card, EmptyState, PageHeader } from '../../components/Layout';
import { Badge, Dot } from '../../components/Status';
import { humanize } from '../../lib/format';
import { Link } from '../../lib/router';
import { isInstalled, launchTarget, signInSummary } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { VaultSetupCard } from './VaultSetup';

function SignInRow({ service }: { service: Service }) {
  const summary = signInSummary(service);
  const identity = service.identity!;
  return (
    <div className="row">
      <Link to={`/apps/${service.id}`} className="row-main">
        <AppIcon id={service.id} />
        <span className="row-text">
          <b>{service.name}</b>
          <small>{identity.state === 'degraded' && identity.detail ? identity.detail : summary.label}</small>
        </span>
      </Link>
      <Badge tone={summary.tone}>
        <Dot tone={summary.tone} />
        {identity.state === 'ready' ? 'Working' : humanize(identity.state)}
      </Badge>
    </div>
  );
}

export function SignInSettings() {
  const { data } = useDashboard();
  const services = data.services.services.filter(
    (service) => isInstalled(service) && service.identity && service.identity.mode !== 'none',
  );
  const sso = services.filter((service) => ['native_oidc', 'trusted_header'].includes(service.identity!.mode));
  const other = services.filter((service) => !sso.includes(service));
  const authentik = data.services.services.find((service) => service.id === 'authentik');
  const authentikTarget = authentik && launchTarget(authentik);
  const working = sso.filter((service) => service.identity!.state === 'ready').length;
  return (
    <>
      <PageHeader
        title="Sign-in"
        description="Manage sign-in for your apps. Compatible apps use Authentik single sign-on; others retain separate accounts. Manage users and access policies in Authentik."
        actions={
          authentikTarget && <ExternalButton href={authentikTarget.url}>Manage users in Authentik</ExternalButton>
        }
      />
      <VaultSetupCard />
      {!services.length ? (
        <EmptyState icon={KeyRound} title="No apps with sign-in yet" />
      ) : (
        <>
          <Card
            title="Single sign-on"
            description={`${working} of ${sso.length} app${sso.length === 1 ? '' : 's'} use your Authentik login.`}
            flush
          >
            <div className="rows">
              {sso.map((service) => (
                <SignInRow key={service.id} service={service} />
              ))}
            </div>
          </Card>
          {other.length > 0 && (
            <Card
              title="Separate logins"
              description="These apps keep their own login. Mu3Lab saves it to your vault, so Bitwarden fills it in when you open the app."
              flush
            >
              <div className="rows">
                {other.map((service) => (
                  <SignInRow key={service.id} service={service} />
                ))}
              </div>
            </Card>
          )}
        </>
      )}
    </>
  );
}
