import { Check, ChevronRight, PackageSearch, Plus, Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button, ExternalButton } from '../../components/Button';
import { EmptyState, PageHeader, Tabs } from '../../components/Layout';
import { Badge, StatusBadge } from '../../components/Status';
import { Link } from '../../lib/router';
import {
  canInstall,
  categoryLabel,
  displayStage,
  isInstalled,
  launchTarget,
  signInSummary,
  type Stage,
  stageLabel,
} from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { InstallProgress } from './InstallProgress';
import { useInstallBatch } from './useInstallBatch';

const STAGE_ORDER: Stage[] = ['optional', 'core', 'foundation'];

function matches(service: Service, term: string, summary = '') {
  return !term || `${service.name} ${service.category} ${summary}`.toLowerCase().includes(term);
}

function InstalledRow({ service }: { service: Service }) {
  const target = launchTarget(service);
  const signIn = signInSummary(service);
  return (
    <div className="row">
      <Link to={`/apps/${service.id}`} className="row-main">
        <AppIcon id={service.id} />
        <span className="row-text">
          <b>{service.name}</b>
          <small>{categoryLabel(service.category)}</small>
        </span>
      </Link>
      <span className="row-meta hide-mobile">
        {service.identity && service.identity.mode !== 'none' && <Badge tone="gray">{signIn.label}</Badge>}
      </span>
      <StatusBadge state={service.state} />
      <span className="row-actions">
        {target ? (
          <ExternalButton size="sm" href={target.url}>
            {target.label}
          </ExternalButton>
        ) : (
          <span className="row-action-placeholder" />
        )}
        <Link
          to={`/apps/${service.id}`}
          className="btn btn-ghost btn-sm btn-icon"
          aria-label={`${service.name} details`}
        >
          <ChevronRight />
        </Link>
      </span>
    </div>
  );
}

function DiscoverCard({
  service,
  summary,
  selected,
  toggle,
}: {
  service: Service;
  summary: string;
  selected: boolean;
  toggle: () => void;
}) {
  const installable = canInstall(service);
  return (
    <article className={`discover-card ${selected ? 'selected' : ''} ${installable ? '' : 'unavailable'}`}>
      <header>
        <AppIcon id={service.id} size="lg" />
        <Link to={`/apps/${service.id}`} className="discover-name">
          <b>{service.name}</b>
          <small>{categoryLabel(service.category)}</small>
        </Link>
      </header>
      <p>{installable ? summary : service.blocked_reason || summary}</p>
      <footer>
        {installable ? (
          <Button
            size="sm"
            variant={selected ? 'primary' : 'secondary'}
            icon={selected ? Check : Plus}
            aria-pressed={selected}
            aria-label={`${selected ? 'Deselect' : 'Select'} ${service.name} for installation`}
            onClick={toggle}
          >
            {selected ? 'Selected' : 'Add'}
          </Button>
        ) : (
          <Badge tone="gray">{service.stage === 'blocked' ? 'Coming later' : 'Unavailable'}</Badge>
        )}
      </footer>
    </article>
  );
}

export function AppsPage({ discover }: { discover: boolean }) {
  const { data } = useDashboard();
  const services = data.services.services;
  const catalog = data.catalog.services;
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const installBatch = useInstallBatch();
  const term = query.trim().toLowerCase();

  const installed = useMemo(() => services.filter(isInstalled), [services]);
  const available = useMemo(
    () => services.filter((service) => !isInstalled(service) && ['optional', 'blocked'].includes(service.stage)),
    [services],
  );
  const shown = (discover ? available : installed).filter((service) =>
    matches(service, term, catalog[service.id]?.summary),
  );
  const names = (ids: string[]) => ids.map((id) => services.find((service) => service.id === id)?.name || id);
  const toggle = (id: string) =>
    setSelected((values) => (values.includes(id) ? values.filter((value) => value !== id) : [...values, id]));

  return (
    <div className="page">
      <PageHeader
        title="Apps"
        description={discover ? 'Add reviewed apps to your private cloud.' : 'Everything running on your Mu3Lab.'}
      />
      <div className="toolbar">
        <Tabs
          label="Apps"
          items={[
            { label: 'Installed', to: '/apps', count: installed.length },
            { label: 'Discover', to: '/apps/discover', count: available.filter(canInstall).length },
          ]}
        />
        <label className="search-input">
          <Search />
          <input
            type="search"
            aria-label="Search apps"
            placeholder="Search apps"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
      </div>
      {installBatch.batch && (
        <InstallProgress
          batch={installBatch.batch}
          job={installBatch.job}
          services={services}
          onAction={(action) => void installBatch.act(action)}
          onDismiss={installBatch.dismiss}
        />
      )}
      {!shown.length ? (
        <EmptyState icon={PackageSearch} title={term ? 'No matching apps' : 'Nothing here yet'}>
          {term
            ? 'Try a different search.'
            : discover
              ? 'Every available app is installed.'
              : 'Install apps from Discover.'}
        </EmptyState>
      ) : discover ? (
        <>
          {(['optional', 'blocked'] as Stage[]).map((stage) => {
            const group = shown.filter((service) => (stage === 'blocked') === (service.stage === 'blocked'));
            if (!group.length) return null;
            return (
              <section key={stage} className="group">
                <h2 className="group-title">{stage === 'blocked' ? 'Coming later' : 'Available to install'}</h2>
                <div className="discover-grid">
                  {group.map((service) => (
                    <DiscoverCard
                      key={service.id}
                      service={service}
                      summary={catalog[service.id]?.summary || service.detail}
                      selected={selected.includes(service.id)}
                      toggle={() => toggle(service.id)}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </>
      ) : (
        STAGE_ORDER.map((stage) => {
          const group = shown.filter((service) => displayStage(service) === stage);
          if (!group.length) return null;
          return (
            <section key={stage} className="group">
              <h2 className="group-title">{stageLabel[stage]}</h2>
              <div className="card card-flush rows">
                {group.map((service) => (
                  <InstalledRow key={service.id} service={service} />
                ))}
              </div>
            </section>
          );
        })
      )}
      {discover && selected.length > 0 && (
        <div className="action-bar" role="region" aria-label="Selected apps">
          <span>
            <b>{selected.length}</b> selected · {names(selected).join(', ')}
          </span>
          <Button variant="ghost" onClick={() => setSelected([])}>
            Clear
          </Button>
          <Button
            variant="primary"
            loading={installBatch.pending}
            onClick={async () => {
              if (await installBatch.install(selected, names(selected))) setSelected([]);
            }}
          >
            Install {selected.length === 1 ? names(selected)[0] : `${selected.length} apps`}
          </Button>
        </div>
      )}
    </div>
  );
}
