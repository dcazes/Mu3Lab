import { ChevronDown, Search } from 'lucide-react';
import { useState } from 'react';
import type { PortsResponse, Service } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Card, EmptyState, PageHeader } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { Link } from '../../lib/router';
import { launcherSections, type Tone } from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

type AppPorts = PortsResponse['apps'][number];
type Entry = AppPorts['entries'][number];

const KIND: Record<Entry['kind'], { label: string; tone: Tone; hint: string }> = {
  tailnet: { label: 'Tailnet', tone: 'blue', hint: 'Opens on any device signed in to your Tailscale network.' },
  home: { label: 'Home', tone: 'green', hint: 'Opens on devices on your home Wi-Fi or plugged into your router.' },
  loopback: {
    label: 'Local port',
    tone: 'gray',
    hint: 'Only programs on the Mu3Lab server itself can reach it; not from a phone or another computer.',
  },
};

/** The port, as a link where a person can open it (tailnet and home addresses). */
function Port({ entry }: { entry: Entry }) {
  const openable = (entry.kind === 'tailnet' || entry.kind === 'home') && /^https?:\/\//.test(entry.address);
  const number = (
    <>
      {entry.port}
      {entry.protocol === 'udp' && <small> UDP</small>}
    </>
  );
  return openable ? (
    <a href={entry.address} target="_blank" rel="noreferrer" title={entry.address}>
      {number}
    </a>
  ) : (
    number
  );
}

/** How each kind of port is reached, said once at the top instead of on every row. */
function Prefixes({ data }: { data: PortsResponse }) {
  const rows: { kind: Entry['kind']; prefix: string }[] = [
    { kind: 'tailnet', prefix: data.tailnet_dns_name ? `https://${data.tailnet_dns_name}:` : '' },
    { kind: 'home', prefix: data.home_ip ? `http://${data.home_ip}:` : '' },
    { kind: 'loopback', prefix: 'http://127.0.0.1:' },
  ];
  return (
    <Card>
      <dl className="ports-prefixes">
        {rows.map(({ kind, prefix }) => (
          <div key={kind}>
            <dt>
              <Badge tone={KIND[kind].tone}>{KIND[kind].label}</Badge>
            </dt>
            <dd>
              {prefix ? (
                <code>
                  {prefix}
                  <em>port</em>
                </code>
              ) : (
                <span className="muted">Not known yet</span>
              )}
              <span className="muted">{KIND[kind].hint}</span>
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function PortsTable({ apps }: { apps: AppPorts[] }) {
  return (
    <Card flush>
      <table className="ports-table">
        <thead>
          <tr>
            <th scope="col">Where</th>
            <th scope="col">Port</th>
            <th scope="col" title="The port inside the app's container, when it differs or is mapped">
              Inside
            </th>
          </tr>
        </thead>
        {apps.map((app) => (
          <tbody key={app.id}>
            <tr className="ports-app">
              <th scope="rowgroup" colSpan={3}>
                <Link to={`/apps/${app.id}`}>
                  <AppIcon id={app.id} size="sm" />
                  {app.name}
                </Link>
              </th>
            </tr>
            {app.entries.map((entry) => (
              <tr key={`${entry.kind}-${entry.port}-${entry.protocol}-${entry.label}`}>
                <td>
                  <span title={KIND[entry.kind].hint}>
                    <Badge tone={KIND[entry.kind].tone}>{KIND[entry.kind].label}</Badge>
                  </span>{' '}
                  <span className="ports-label">{entry.label}</span>
                </td>
                <td className="ports-number">
                  <Port entry={entry} />
                </td>
                <td className="ports-number">{entry.container_port ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </Card>
  );
}

/** The ports grouped like the Apps page: everyday apps by category, the stacks, then the rest. */
function sectionsOf(apps: AppPorts[], services: Service[]) {
  const listed = new Map(apps.map((app) => [app.id, app]));
  const sections = launcherSections(services.filter((service) => listed.has(service.id))).map((section) => ({
    id: section.id,
    label: section.label,
    apps: section.apps.map((service) => listed.get(service.id)).filter((app): app is AppPorts => Boolean(app)),
  }));
  const placed = new Set(sections.flatMap((section) => section.apps.map((app) => app.id)));
  const other = apps.filter((app) => !placed.has(app.id));
  return other.length ? [...sections, { id: 'other', label: 'Other', apps: other }] : sections;
}

/** Settings > Ports: every installed app's ports on this server, grouped like the Apps page. */
export function PortsSettings() {
  const { data, error, loading } = useApi<PortsResponse>('/api/v1/ports');
  const { data: dashboard } = useDashboard();
  const [query, setQuery] = useState('');
  const [folded, setFolded] = useState<string[]>([]);
  const term = query.trim().toLowerCase();
  const apps = (data?.apps || []).filter(
    (app) =>
      !term ||
      app.name.toLowerCase().includes(term) ||
      app.entries.some((entry) =>
        `${entry.port} ${entry.container_port ?? ''} ${entry.label}`.toLowerCase().includes(term),
      ),
  );
  const sections = sectionsOf(apps, dashboard.services.services);
  const toggle = (id: string) =>
    setFolded((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  return (
    <>
      <PageHeader title="Ports" description="Every port each installed app uses on this server, and how to reach it." />
      {data && <Prefixes data={data} />}
      <div className="ports-toolbar">
        <label className="search-input">
          <Search />
          <input
            type="search"
            aria-label="Search apps or ports"
            placeholder="App or port"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
      </div>
      {error ? (
        <EmptyState title="Ports could not be loaded">{error}</EmptyState>
      ) : !data && loading ? (
        <p className="muted">Loading…</p>
      ) : !apps.length ? (
        <EmptyState title={term ? 'Nothing matches' : 'No apps installed'}>
          {term ? 'Try an app name or a port number.' : 'Installed apps show their ports here.'}
        </EmptyState>
      ) : (
        sections.map((section) => {
          // A search opens every section, so nothing that matches is hidden.
          const open = Boolean(term) || !folded.includes(section.id);
          return (
            <section key={section.id} className="ports-section" aria-label={section.label}>
              <h3>
                <button
                  type="button"
                  className="launcher-toggle"
                  aria-expanded={open}
                  onClick={() => toggle(section.id)}
                >
                  <ChevronDown className={open ? undefined : 'is-closed'} />
                  {section.label}
                </button>
                <span className="muted">
                  {section.apps.length} {section.apps.length === 1 ? 'app' : 'apps'}
                </span>
              </h3>
              {open && <PortsTable apps={section.apps} />}
            </section>
          );
        })
      )}
    </>
  );
}
