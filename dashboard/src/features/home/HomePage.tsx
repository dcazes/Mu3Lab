import { ArrowRight, X } from 'lucide-react';
import { Link } from '../../lib/router';
import { isInstalled } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { AppStacks } from '../apps/AppStacks';
import { CoreSetupProgress } from './CoreSetupProgress';
import { GetStarted } from './GetStarted';
import { SystemStatus } from './SystemStatus';
import { useHomeNotices } from './warnings';

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

function Attention() {
  const { notices, progress, dismiss } = useHomeNotices();
  const items = [...notices, ...progress];
  if (!items.length) return null;
  return (
    <section className="attention" aria-label="Needs attention">
      {items.map(({ key, warnings, icon: Icon, tone, text, more, to }) => (
        <div key={key} className="attention-row">
          <Link to={to} className={`attention-item attention-${tone}`}>
            <Icon className={tone === 'progress' ? 'spin' : ''} />
            <span>
              {text}
              {more > 0 && <small className="attention-more"> +{more} more</small>}
            </span>
            <ArrowRight className="attention-arrow" />
          </Link>
          {warnings.length > 0 && (
            <button
              type="button"
              className="btn btn-ghost btn-sm btn-icon"
              aria-label="Hide this warning until it changes"
              title="Hide until it changes"
              onClick={() => dismiss(warnings)}
            >
              <X />
            </button>
          )}
        </div>
      ))}
    </section>
  );
}

export function HomePage() {
  const { data } = useDashboard();
  const installed = data.services.services.filter(isInstalled);
  const name = (data.identity.display_name || data.identity.username || '').split(' ')[0];
  return (
    <div className="home">
      <header className="home-header">
        <div>
          <h1>
            {greeting()}
            {name ? `, ${name}` : ''}
          </h1>
          <p>{new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</p>
        </div>
        <SystemStatus />
      </header>
      <Attention />
      <CoreSetupProgress />
      <GetStarted />
      <section aria-labelledby="apps-heading">
        <AppStacks services={installed} title="Your apps" />
      </section>
    </div>
  );
}
