import { Fragment } from 'react';
import { EmptyState } from '../../components/Layout';
import { GetStartedSettings } from '../home/GetStarted';
import { Link } from '../../lib/router';
import { AiSettings } from './AiSettings';
import { IntegrationsSettings } from './IntegrationsSettings';
import { useIsAdmin } from '../../state/dashboard';
import { PeopleSettings } from './PeopleSettings';
import { settingsSectionsFor } from './sections';
import { SecuritySettings } from './SecuritySettings';
import { SignInSettings } from './SignInSettings';
import { SystemSettings } from './SystemSettings';
import { VoiceSettings } from './VoiceSettings';
import { WhatsNew } from './WhatsNew';

const PAGES: Record<string, () => JSX.Element> = {
  'get-started': GetStartedSettings,
  ai: AiSettings,
  integrations: IntegrationsSettings,
  'sign-in': SignInSettings,
  voice: VoiceSettings,
  people: PeopleSettings,
  security: SecuritySettings,
  system: SystemSettings,
  'whats-new': WhatsNew,
};

export function SettingsPage({ section }: { section: string }) {
  const sections = settingsSectionsFor(useIsAdmin());
  const current = sections.find((item) => item.id === section);
  const Page = current ? PAGES[section] : undefined;
  const highlighted = current?.within || section;
  return (
    <div className="settings">
      <nav className="settings-nav" aria-label="Settings">
        <h1 className="settings-nav-title">Settings</h1>
        {sections
          .filter((item) => !item.within)
          .map(({ id, label, icon: Icon, group }, index, shown) => (
            <Fragment key={id}>
              {group !== shown[index - 1]?.group && <h2 className="settings-nav-group">{group}</h2>}
              <Link
                to={`/settings/${id}`}
                className={id === highlighted ? 'active' : ''}
                aria-current={id === section ? 'page' : undefined}
              >
                <Icon />
                {label}
              </Link>
            </Fragment>
          ))}
      </nav>
      <div className="settings-content page">{Page ? <Page /> : <EmptyState title="Settings page not found" />}</div>
    </div>
  );
}
