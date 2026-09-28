import { EmptyState } from '../../components/Layout';
import { Link } from '../../lib/router';
import { AiSettings } from './AiSettings';
import { CalendarSettings } from './CalendarSettings';
import { IntegrationsSettings } from './IntegrationsSettings';
import { SETTINGS_SECTIONS } from './sections';
import { SecuritySettings } from './SecuritySettings';
import { SignInSettings } from './SignInSettings';
import { SystemSettings } from './SystemSettings';

const PAGES: Record<string, () => JSX.Element> = {
  ai: AiSettings,
  integrations: IntegrationsSettings,
  'sign-in': SignInSettings,
  calendar: CalendarSettings,
  security: SecuritySettings,
  system: SystemSettings,
};

export function SettingsPage({ section }: { section: string }) {
  const Page = PAGES[section];
  return (
    <div className="settings">
      <nav className="settings-nav" aria-label="Settings">
        <h1 className="settings-nav-title">Settings</h1>
        {SETTINGS_SECTIONS.map(({ id, label, icon: Icon }) => (
          <Link
            key={id}
            to={`/settings/${id}`}
            className={id === section ? 'active' : ''}
            aria-current={id === section ? 'page' : undefined}
          >
            <Icon />
            {label}
          </Link>
        ))}
      </nav>
      <div className="settings-content page">{Page ? <Page /> : <EmptyState title="Settings page not found" />}</div>
    </div>
  );
}
