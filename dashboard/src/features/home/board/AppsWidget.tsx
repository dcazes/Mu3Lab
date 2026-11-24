import { ArrowRight } from 'lucide-react';
import { AppIcon } from '../../../components/AppIcon';
import { Link } from '../../../lib/router';
import {
  isEverydayApp,
  isInstalled,
  isListed,
  isRunning,
  isWorking,
  launchTarget,
  needsAttention,
} from '../../../lib/services';
import { useDashboard } from '../../../state/dashboard';
import { FitList, useBox } from './size';
import type { WidgetProps } from './widgets';

/**
 * How the installed apps are doing, with the running ones a click away. Everything else (stacks,
 * starting and stopping, details) is on Apps, which "All apps" opens.
 */
export function AppsWidget(_props: WidgetProps) {
  const { data } = useDashboard();
  const { width, height } = useBox();
  const measured = width > 0;
  const installed = data.services.services.filter((service) => isInstalled(service) && isListed(service));
  const running = installed.filter(isRunning);
  const broken = installed.filter(needsAttention);
  const busy = installed.filter(isWorking);
  // Everyday apps a person opens; the plumbing behind them (sign-in, AI, backups) counts but has no icon here.
  const shown = running.filter((service) => isEverydayApp(service) && launchTarget(service));
  // Names under the icons once there is room for them; the title already links to Apps, so a narrow
  // widget leaves out "All apps".
  const named = !measured || height >= 150;
  return (
    <div className={`apps-widget${named ? ' is-named' : ''}`}>
      <div className="apps-widget-summary">
        <p>
          <strong>{running.length}</strong> of {installed.length} running
        </p>
        {broken.length > 0 && (
          <Link to="/apps" className="apps-widget-flag tone-red">
            {broken.length} need{broken.length === 1 ? 's' : ''} attention
          </Link>
        )}
        {busy.length > 0 && <span className="apps-widget-flag tone-blue">{busy.length} busy</span>}
        {(!measured || width >= 300) && (
          <Link to="/apps" className="widget-link apps-widget-all">
            All apps
            <ArrowRight />
          </Link>
        )}
      </div>
      {shown.length > 0 ? (
        // As many icons as fit; the count above and the title (which opens Apps) cover the rest.
        <FitList className="apps-widget-icons" label="Running apps">
          {shown.map((service) => {
            const target = launchTarget(service);
            return (
              <li key={service.id}>
                <a
                  href={target?.url}
                  target="_blank"
                  rel="noreferrer"
                  title={`Open ${service.name}`}
                  aria-label={`Open ${service.name}`}
                >
                  <AppIcon id={service.id} />
                  <span className="apps-widget-name">{service.name}</span>
                </a>
              </li>
            );
          })}
        </FitList>
      ) : (
        <p className="muted">No everyday apps are running.</p>
      )}
    </div>
  );
}
