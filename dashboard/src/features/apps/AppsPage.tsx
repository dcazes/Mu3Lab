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
  CATEGORIES,
  canInstall,
  categoryLabel,
  type Group,
  groupLabel,
  isInstalled,
  launchTarget,
  signInSummary,
} from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';
import { InstallPlanDialog, sizeLabel } from './InstallPlanDialog';
import { InstallProgress } from './InstallProgress';
import { activeJob, JobProgress } from './JobProgress';
import { useInstallBatch } from './useInstallBatch';

const GROUP_ORDER: Group[] = ['apps', 'ai', 'infrastructure'];
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
  return !term || `${service.name} ${categoryLabel(service.category)} ${summary}`.toLowerCase().includes(term);
}

/** Every installed row shares one set of columns, so states line up down the page. */
function InstalledHeader() {
  return (
    <div className="row installed-row installed-head hide-mobile" aria-hidden="true">
      <span>App</span>
      <span>Status</span>
      <span>Sign-in</span>
      <span>Update</span>
      <span />
      <span />
    </div>
  );
}

function InstalledRow({ service, summary }: { service: Service; summary: string }) {
  const target = launchTarget(service);
  const signIn = signInSummary(service);
  return (
    <div className="row installed-row">
      <Link to={`/apps/${service.id}`} className="row-main">
        <AppIcon id={service.id} />
        <span className="row-text">
          <b>{service.name}</b>
          <small>{summary || categoryLabel(service.category)}</small>
        </span>
      </Link>
      <span className="installed-cell">
        <StatusBadge state={service.display_state} />
      </span>
      <span className="installed-cell hide-mobile">
        {service.identity && service.identity.mode !== 'none' && <Badge tone="gray">{signIn.label}</Badge>}
      </span>
      <span className="installed-cell hide-mobile">
        {service.update?.update_available && <Badge tone="blue">Update ready</Badge>}
      </span>
      <span className="installed-cell">
        {/* Always "Open", so every button is the same width; the specific label is its tooltip. */}
        {target && (
          <ExternalButton size="sm" href={target.url} className="installed-open" title={target.label}>
            Open
          </ExternalButton>
        )}
      </span>
      <Link to={`/apps/${service.id}`} className="btn btn-ghost btn-sm btn-icon" aria-label={`${service.name} details`}>
        <ChevronRight />
      </Link>
    </div>
  );
}

function DiscoverCard({
  service,
  summary,
  size,
  selected,
  installing,
  toggle,
}: {
  service: Service;
  summary: string;
  size?: AppSize;
  selected: boolean;
  installing?: string;
  toggle: () => void;
}) {
  const installable = canInstall(service) && !installing;
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
      <p>{summary}</p>
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
        ) : installing ? (
          <Button size="sm" disabled>
            {installing}
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
    () => services.filter((service) => !isInstalled(service) && service.stage === 'optional'),
    [services],
  );
  const queued = new Map(
    installBatch.batch && ['queued', 'running', 'paused', 'resetting'].includes(installBatch.batch.state)
      ? installBatch.batch.items
          .filter((item) => ['pending', 'queued', 'running', 'resetting'].includes(item.state))
          .map((item) => [
            item.service_id,
            item.state === 'pending'
              ? item.download_state === 'downloading'
                ? 'Downloading'
                : item.download_state === 'paused'
                  ? 'Paused'
                  : 'Queued'
              : 'Installing',
          ])
      : [],
  );
  // Installs, retries and updates started from an app's own page; the batch card covers its own jobs.
  const batchJobs = new Set((installBatch.batch?.items || []).map((item) => item.job_id).filter(Boolean));
  const singleJobs = services.filter((service) => {
    const job = activeJob(service);
    return job && !batchJobs.has(job.id);
  });
  // Drop picks that have since been installed or joined the queue.
  const selected = stored.filter((id) =>
    available.some((service) => service.id === id && canInstall(service) && !queued.has(id)),
  );
  const sizes = useApi<AppSizesResponse>(
    discover ? `/api/v1/app-sizes?ids=${encodeURIComponent(selected.join(','))}` : null,
    // The worker measures sizes in the background; ask again only until it has.
    { interval: (data) => (data && data.measured ? 0 : 10000) },
  ).data;
  const shown = (discover ? available : installed).filter((service) =>
    matches(service, term, catalog[service.id]?.summary),
  );
  // Discover's sections in catalog order; an app with an unknown category still shows, last.
  const sections = [
    ...CATEGORIES,
    ...[...new Set(shown.map((service) => service.category))]
      .filter((id) => !CATEGORIES.some((item) => item.id === id))
      .map((id) => ({ id, label: categoryLabel(id) })),
  ]
    .map((category) => ({ ...category, apps: shown.filter((service) => service.category === category.id) }))
    .filter((category) => category.apps.length > 0);
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
          controls={installBatch.controls}
        />
      )}
      {singleJobs.map((service) => (
        <JobProgress key={service.id} service={service} showName />
      ))}
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
          <nav className="category-chips" aria-label="Categories">
            {sections.map(({ id, label, apps }) => (
              <a key={id} href={`#category-${id}`} className="category-chip">
                {label}
                <span>{apps.length}</span>
              </a>
            ))}
          </nav>
          {sections.map(({ id, label, apps }) => (
            <section key={id} className="group" id={`category-${id}`} aria-labelledby={`category-${id}-title`}>
              <h2 className="group-title" id={`category-${id}-title`}>
                {label}
              </h2>
              <div className="discover-grid">
                {apps.map((service) => (
                  <DiscoverCard
                    key={service.id}
                    service={service}
                    summary={catalog[service.id]?.tagline || catalog[service.id]?.summary || service.detail}
                    size={sizes?.apps?.[service.id]}
                    selected={selected.includes(service.id)}
                    installing={queued.get(service.id)}
                    toggle={() => toggle(service.id)}
                  />
                ))}
              </div>
            </section>
          ))}
        </>
      ) : (
        GROUP_ORDER.map((name) => {
          const group = shown.filter((service) => service.group === name);
          if (!group.length) return null;
          return (
            <section key={name} className="group">
              <h2 className="group-title">{groupLabel[name]}</h2>
              <div className="card card-flush rows">
                <InstalledHeader />
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
            {sizes?.measured && sizes.selection && (
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
