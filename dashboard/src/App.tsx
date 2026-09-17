import { useEffect, useMemo, useState } from 'react';
import { api, CatalogResponse, Health, IntegrationsResponse, Service, ServicesResponse, SystemResponse } from './api';

type Tab = 'workspace' | 'catalog' | 'system';
const tabs: { id: Tab; label: string }[] = [
  { id: 'workspace', label: 'Workspace' }, { id: 'catalog', label: 'Catalog' }, { id: 'system', label: 'System' },
];
const icons: Record<string, string> = { ingress: '↗', authentik: '◇', vaultwarden: '▣', ollama: '◉', litellm: '⌁', 'open-webui': '◌', freellmapi: '✦', surfsense: '⌕' };
const labels: Record<Service['state'], string> = { healthy: 'Online', blocked: 'Unavailable by policy', not_installed: 'Not installed', stopped_or_unhealthy: 'Needs attention' };

function formatBytes(bytes: number) { const units = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; let value = bytes; while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; } return `${value.toFixed(i < 3 ? 0 : 1)} ${units[i]}`; }
function Metric({ label, value, percent }: { label: string; value: string; percent?: number }) { return <div className="metric"><span>{label}</span><strong>{value}</strong>{percent !== undefined && <i><b style={{ width: `${Math.min(percent, 100)}%` }} /></i>}</div>; }

function ServiceCard({ service, selected, onSelect }: { service: Service; selected: boolean; onSelect: () => void }) {
  return <button className={`service-card ${selected ? 'selected' : ''}`} onClick={onSelect}><span className="app-icon">{icons[service.id] || '◇'}</span><span className="service-card-copy"><strong>{service.name}</strong><small>{service.category}</small></span><span className={`status-dot ${service.state}`} title={labels[service.state]} /></button>;
}

function Inspector({ service, catalog }: { service: Service; catalog: CatalogResponse | null }) {
  const info = catalog?.services[service.id];
  const auth = service.auth === 'oidc' ? 'Authentik OIDC' : service.auth === 'proxy' ? 'Access gate' : service.auth === 'excluded' ? 'Not exposed' : 'Local account';
  return <section className="inspector panel"><header><span className="app-icon large">{icons[service.id] || '◇'}</span><div><span className={`state ${service.state}`}>{labels[service.state]}</span><h2>{service.name}</h2><p>{info?.summary || service.detail}</p></div></header><dl><div><dt>Identity</dt><dd>{auth}</dd></div><div><dt>Lifecycle</dt><dd>{service.lifecycle.replace('_', ' ')}</dd></div><div><dt>Hardware</dt><dd>{service.profiles.join(' · ')}</dd></div><div><dt>MCP</dt><dd>{service.mcp.exposed ? `Curated ${service.mcp.risk}` : 'Not exposed'}</dd></div></dl>{service.dependencies.length > 0 && <p className="notice">Depends on {service.dependencies.join(', ')}.</p>}{service.state === 'blocked' && <p className="notice warning">{service.blocked_reason}</p>}<div className="actions">{service.url && service.state === 'healthy' ? <a href={service.url} target="_blank" rel="noreferrer">Open securely ↗</a> : <button disabled>{service.state === 'blocked' ? 'Not offered' : 'Start controls arrive next'}</button>}<button disabled title="Lifecycle jobs are being added through the safe control plane">Configure</button></div><p className="muted">Lifecycle controls remain disabled until safe, audited Compose jobs are available.</p></section>;
}

function Workspace({ services, catalog, system }: { services: Service[]; catalog: CatalogResponse | null; system: SystemResponse | null }) {
  const [query, setQuery] = useState(''); const [selectedId, setSelectedId] = useState('');
  const visible = useMemo(() => services.filter(s => `${s.name} ${s.category}`.toLowerCase().includes(query.toLowerCase())), [services, query]);
  const selected = visible.find(s => s.id === selectedId) || visible.find(s => s.state === 'healthy') || visible[0]; const online = services.filter(s => s.state === 'healthy').length;
  if (!selected) return <section className="panel empty">No curated services are registered yet.</section>;
  return <><section className="metrics panel"><Metric label="CPU" value={system ? `${Math.round(system.cpu_percent)}%` : '—'} percent={system?.cpu_percent} /><Metric label="Memory" value={system ? `${Math.round(system.memory.percent)}%` : '—'} percent={system?.memory.percent} /><Metric label="Disk free" value={system ? formatBytes(system.disk.total - system.disk.used) : '—'} percent={system?.disk.percent} /><Metric label="Services" value={`${online}/${services.length} online`} percent={services.length ? online / services.length * 100 : 0} /></section><section className="workspace-toolbar"><label>Apps <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Filter curated apps…" /></label><span>{visible.length} shown</span></section><section className="workspace-grid"><div className="service-list panel">{visible.map(service => <ServiceCard key={service.id} service={service} selected={selected.id === service.id} onSelect={() => setSelectedId(service.id)} />)}</div><Inspector service={selected} catalog={catalog} /></section></>;
}

function Catalog({ catalog, services }: { catalog: CatalogResponse | null; services: Service[] }) {
  if (!catalog) return <section className="panel empty">Catalog is loading…</section>; const states = new Map(services.map(s => [s.id, s]));
  return <div className="catalog-grid">{catalog.profiles.map(profile => <section className="panel profile" key={profile.id}><span className="eyebrow">CURATED PROFILE</span><h2>{profile.name}</h2><p>{profile.services.length} coordinated services</p><ul>{profile.services.map(id => { const service = states.get(id); return <li key={id}><span>{icons[id] || '◇'}</span>{service?.name || id}<small>{service ? labels[service.state] : 'Unknown'}</small></li>; })}</ul><button disabled>Profile setup arrives with safe jobs</button></section>)}</div>;
}

function System({ system, integrations }: { system: SystemResponse | null; integrations: IntegrationsResponse | null }) {
  return <div className="system-grid"><section className="panel"><span className="eyebrow">FOUNDATION</span><h2>Private platform status</h2><dl className="foundation"><div><dt>Tailnet</dt><dd>{system?.tailnet_dns_name || 'Not connected'}</dd></div><div><dt>Docker</dt><dd>{system?.docker_ready ? 'Ready' : 'Unavailable'}</dd></div><div><dt>Runtime</dt><dd>{system?.runtime_root || 'Loading…'}</dd></div><div><dt>Backups</dt><dd>{system?.backup.ready ? 'Ready' : (system?.backup.detail || 'Not configured')}</dd></div></dl></section><section className="panel"><span className="eyebrow">AI ROUTING</span><h2>Free-first model path</h2><p>{integrations?.policy ? 'Paid routes are always explicit; they are never selected automatically.' : 'Loading integration policy…'}</p><div className="wiring">{integrations?.integrations.map(item => <div key={`${item.source}-${item.destination}`}><b>{item.source}</b><span>→</span><b>{item.destination}</b><small>{item.kind.replace('_', ' ')}</small></div>)}</div></section></div>;
}

export default function App() {
  const [tab, setTab] = useState<Tab>('workspace'); const [health, setHealth] = useState<Health | null>(null); const [services, setServices] = useState<ServicesResponse | null>(null); const [catalog, setCatalog] = useState<CatalogResponse | null>(null); const [system, setSystem] = useState<SystemResponse | null>(null); const [integrations, setIntegrations] = useState<IntegrationsResponse | null>(null); const [error, setError] = useState('');
  useEffect(() => { let alive = true; const load = () => Promise.all([api<Health>('/api/health'), api<ServicesResponse>('/api/services'), api<CatalogResponse>('/api/catalog'), api<SystemResponse>('/api/system'), api<IntegrationsResponse>('/api/integrations')]).then(([h, s, c, sy, i]) => { if (!alive) return; setHealth(h); setServices(s); setCatalog(c); setSystem(sy); setIntegrations(i); setError(''); }).catch(e => alive && setError(e instanceof Error ? e.message : String(e))); load(); const timer = window.setInterval(load, 10000); return () => { alive = false; window.clearInterval(timer); }; }, []);
  return <div className="app-shell"><header className="header"><div className="brand"><span>μ</span><div><h1>Mu3Lab</h1><p>Private app platform</p></div></div><div className="header-status"><i className={health?.ok ? 'online' : ''} />{health?.ok ? 'Control plane online' : 'Connecting…'}</div></header><main><nav role="tablist">{tabs.map(item => <button key={item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}>{item.label}</button>)}</nav>{error && <div className="error">Dashboard data is unavailable: {error}</div>}{services ? <div className="content">{tab === 'workspace' && <Workspace services={services.services} catalog={catalog} system={system} />}{tab === 'catalog' && <Catalog catalog={catalog} services={services.services} />}{tab === 'system' && <System system={system} integrations={integrations} />}</div> : <div className="loading">Loading the Mu3Lab control plane…</div>}</main></div>;
}
