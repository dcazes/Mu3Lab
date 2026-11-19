import { Search } from 'lucide-react';
import { useState } from 'react';
import type { PortsResponse } from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Card, EmptyState, PageHeader } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { Link } from '../../lib/router';
import type { Tone } from '../../lib/services';
import { useApi } from '../../lib/useApi';

type Entry = PortsResponse['apps'][number]['entries'][number];

const KIND: Record<Entry['kind'], { label: string; tone: Tone; hint: string }> = {
  tailnet: { label: 'Tailnet', tone: 'blue', hint: 'Opens on any device signed in to your Tailscale network.' },
  home: { label: 'Home', tone: 'green', hint: 'Opens on devices on your home Wi-Fi or plugged into your router.' },
  loopback: { label: 'This server', tone: 'gray', hint: 'Only programs on the Mu3Lab server itself can reach it.' },
  proxy: { label: 'Proxy', tone: 'gray', hint: 'Caddy listens here and passes signed-in visitors on to the app.' },
};

function Address({ entry }: { entry: Entry }) {
  if (!entry.address) return <span className="muted">Not known yet</span>;
  // Addresses a person opens are links; the server-only ones are just text to copy.
  const openable = (entry.kind === 'tailnet' || entry.kind === 'home') && /^https?:\/\//.test(entry.address);
  return openable ? (
    <a href={entry.address} target="_blank" rel="noreferrer" className="ports-address">
      {entry.address}
    </a>
  ) : (
    <code className="ports-address">{entry.address}</code>
  );
}

/** Settings > Ports: every installed app's addresses and the ports it holds on the server, in one place. */
export function PortsSettings() {
  const { data, error, loading } = useApi<PortsResponse>('/api/v1/ports');
  const [query, setQuery] = useState('');
  const term = query.trim().toLowerCase();
  const apps = (data?.apps || []).filter(
    (app) =>
      !term ||
      app.name.toLowerCase().includes(term) ||
      app.entries.some((entry) => `${entry.address} ${entry.port} ${entry.container_port ?? ''}`.includes(term)),
  );
  return (
    <>
      <PageHeader
        title="Ports"
        description="Where each installed app can be opened, and every port it uses on this server."
      />
      <div className="ports-toolbar">
        <div className="ports-legend">
          {Object.values(KIND).map(({ label, tone, hint }) => (
            <span key={label} title={hint}>
              <Badge tone={tone}>{label}</Badge>
            </span>
          ))}
        </div>
        <label className="search-input">
          <Search />
          <input
            type="search"
            aria-label="Search apps, addresses or ports"
            placeholder="App, address or port"
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
          {term ? 'Try an app name, an address or a port number.' : 'Installed apps show their ports here.'}
        </EmptyState>
      ) : (
        <Card flush>
          <table className="ports-table">
            <thead>
              <tr>
                <th scope="col">Where</th>
                <th scope="col">Address</th>
                <th scope="col">Port</th>
                <th scope="col" title="The port inside the app's container, when it differs or is mapped">
                  Inside
                </th>
              </tr>
            </thead>
            {apps.map((app) => (
              <tbody key={app.id}>
                <tr className="ports-app">
                  <th scope="rowgroup" colSpan={4}>
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
                    <td>
                      <Address entry={entry} />
                    </td>
                    <td className="ports-number">
                      {entry.port}
                      {entry.protocol === 'udp' && <small> UDP</small>}
                    </td>
                    <td className="ports-number">{entry.container_port ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </Card>
      )}
    </>
  );
}
