import { Check, ChevronRight, PackageSearch, Plus, Search } from 'lucide-react';
import { type MouseEvent, useEffect, useMemo, useState } from 'react';
import type { AppSize, AppSizesResponse, Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button, ExternalButton } from '../../components/Button';
import { EmptyState, PageHeader, Tabs } from '../../components/Layout';
import { Badge, StatusBadge } from '../../components/Status';
import { bytes } from '../../lib/format';
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
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';
import { InstallPlanDialog, sizeLabel } from './InstallPlanDialog';
import { InstallProgress } from './InstallProgress';
import { useInstallBatch } from './useInstallBatch';

const STAGE_ORDER: Stage[] = ['optional', 'core', 'foundation'];
const SELECTION_KEY = 'mu3lab.discoverSelection';

/** Discover picks survive a visit to an app's detail page and back. */
function useSelection() {
  const [selected, setSelected] = useState<string[]>(() => {
    try {
      const value: unknown = JSON.parse(sessionStorage.getItem(SELECTION_KEY) || '[]');
      return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
    } catch {
      return [];
    }
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(SELECTION_KEY, JSON.stringify(selected));
    } catch {
      // Storage can be unavailable (private mode); the selection just won't persist.
    }
  }, [selected]);
  return [selected, setSelected] as const;
}

function matches(service: Service, term: string, summary = '') {
  return !term || `${service.name} ${service.category} ${summary}`.toLowerCase().includes(term);
}

function InstalledRow({ service, summary }: { service: Service; summary: string }) {
  const target = launchTarget(service);
  const signIn = signInSummary(service);
  return (
    <div className="row">
      <Link to={`/apps/${service.id}`} className="row-main">
        <AppIcon id={service.id} />
        <span className="row-text">
          <b>{service.name}</b>
          <small>{summary || categoryLabel(service.category)}</small>
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
  size,
  selected,
  toggle,
}: {
  service: Service;
  summary: string;
  size?: AppSize;
  selected: boolean;
  toggle: () => void;
}) {
  const installable = canInstall(service);
  const keep = (event: MouseEvent) => event.stopPropagation();
  return (
    // The whole card toggles the selection (the button is its keyboard equivalent);
    // only the icon and name open the app's details.
    <article
      className={`discover-card ${selected ? 'selected' : ''} ${installable ? 'selectable' : 'unavailable'}`}
      onClick={installable ? toggle : undefined}
    >
      <header>
        <Link to={`/apps/${service.id}`} className="discover-link" onClick={keep}>
          <AppIcon id={service.id} size="lg" />
          <span className="discover-name">
            <b>{service.name}</b>
            <small>{categoryLabel(service.category)}</small>
          </span>
        </Link>
      </header>
      <p>{installable ? summary : service.blocked_reason || summary}</p>
      {installable && <small className="discover-size">{sizeLabel(size)}</small>}
      <footer>
        {installable ? (
          <Button
            size="sm"
            variant={selected ? 'primary' : 'secondary'}
            icon={selected ? Check : Plus}
            aria-pressed={selected}
            aria-label={`${selected ? 'Deselect' : 'Select'} ${service.name} for installation`}
            onClick={(event) => {
              keep(event);
              toggle();
            }}
          >
            {selected ? 'Selected' : 'Add'}
          </Button>
        ) : (
          <Badge tone="gray">Unavailable</Badge>
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
  const [stored, setSelected] = useSelection();
  const [planning, setPlanning] = useState(false);
  const installBatch = useInstallBatch();
  const term = query.trim().toLowerCase();

  const installed = useMemo(() => services.filter(isInstalled), [services]);
  const available = useMemo(
    () => services.filter((service) => !isInstalled(service) && ['optional', 'blocked'].includes(service.stage)),
    [services],
  );
  // Drop picks that have since been installed or become unavailable.
  const selected = stored.filter((id) => available.some((service) => service.id === id && canInstall(service)));
  // Sizes are measured in the background by the worker; re-read them now and then.
  const sizes = useApi<AppSizesResponse>(
    discover ? `/api/v1/app-sizes?ids=${encodeURIComponent(selected.join(','))}` : null,
    { interval: 30000 },
  ).data;
  const shown = (discover ? available : installed).filter((service) =>
    matches(service, term, catalog[service.id]?.summary),
  );
  const names = (ids: string[]) => ids.map((id) => services.find((service) => service.id === id)?.name || id);
  const toggle = (id: string) =>
    setSelected(selected.includes(id) ? selected.filter((value) => value !== id) : [...selected, id]);

  return (
    <div className="page">
      <PageHeader
        title="Apps"
        description={
          discover
            ? 'Explore apps you can install on your Mu3Lab server.'
            : 'Manage installed apps and supporting services.'
        }
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
          controls={installBatch.controls}
        />
      )}
      {!shown.length ? (
        <EmptyState
          icon={PackageSearch}
          title={term ? 'No matching apps' : discover ? 'No apps available to install' : 'No apps installed'}
        >
          {term
            ? 'Try a different search.'
            : discover
              ? 'No additional apps are currently available to install.'
              : 'Open Discover to choose apps to install.'}
        </EmptyState>
      ) : discover ? (
        <>
          {(['optional', 'blocked'] as Stage[]).map((stage) => {
            const group = shown.filter((service) => (stage === 'blocked') === (service.stage === 'blocked'));
            if (!group.length) return null;
            return (
              <section key={stage} className="group">
                <h2 className="group-title">{stage === 'blocked' ? 'Unavailable' : 'Available to install'}</h2>
                <div className="discover-grid">
                  {group.map((service) => (
                    <DiscoverCard
                      key={service.id}
                      service={service}
                      summary={catalog[service.id]?.tagline || catalog[service.id]?.summary || service.detail}
                      size={sizes?.apps[service.id]}
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
                  <InstalledRow
                    key={service.id}
                    service={service}
                    summary={catalog[service.id]?.tagline || catalog[service.id]?.summary || ''}
                  />
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
            {sizes?.measured && (
              <small className="action-bar-size">
                {' '}
                · {bytes(sizes.selection.needed_bytes)} to download · about {bytes(sizes.selection.needed_disk_bytes)}{' '}
                of disk space
              </small>
            )}
          </span>
          <Button variant="ghost" onClick={() => setSelected([])}>
            Clear
          </Button>
          <Button variant="primary" loading={installBatch.pending} onClick={() => setPlanning(true)}>
            Install {selected.length === 1 ? names(selected)[0] : `${selected.length} apps`}
          </Button>
        </div>
      )}
      {planning && (
        <InstallPlanDialog
          ids={selected}
          names={Object.fromEntries(selected.map((id, index) => [id, names(selected)[index]]))}
          sizes={sizes}
          pending={installBatch.pending}
          onClose={() => setPlanning(false)}
          onInstall={async (order, parallel) => {
            if (await installBatch.install(order, parallel)) {
              setSelected([]);
              setPlanning(false);
            }
          }}
        />
      )}
    </div>
  );
}
