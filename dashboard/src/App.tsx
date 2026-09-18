import { useEffect, useState } from 'react';
import { api, AuditResponse, CatalogResponse, CoreSetupResponse, Health, IdentityResponse, IntegrationsResponse, JobsResponse, ProvisioningResponse, ServicesResponse, SystemResponse } from './api';
import { AiMcpPanel, AppsPanel, HomePanel, ProtectionPanel, SystemPanel } from './panels';

type Tab = 'home' | 'apps' | 'connections' | 'security' | 'system';
const tabs: { id: Tab; label: string; group?: string; path: string }[] = [
  { id: 'home', label: 'Home', group: 'Platform', path: '/' }, { id: 'apps', label: 'My Apps', path: '/apps' },
  { id: 'connections', label: 'Connections', path: '/connections' },
  { id: 'security', label: 'Security & Backups', group: 'Protection', path: '/security' },
  { id: 'system', label: 'System', group: 'Host', path: '/system' },
];
type DashboardData = { health: Health; services: ServicesResponse; catalog: CatalogResponse; system: SystemResponse; integrations: IntegrationsResponse; identity: IdentityResponse; jobs: JobsResponse; audit: AuditResponse; core: CoreSetupResponse; provisioning: ProvisioningResponse };
function routeTab(path: string): Tab { if (path === '/' || path === '') return 'home'; if (path.startsWith('/apps')) return 'apps'; if (path.startsWith('/connections')) return 'connections'; if (path.startsWith('/security')) return 'security'; if (path.startsWith('/system')) return 'system'; return 'home'; }
function knownRoute(path: string, services: { id: string }[]): boolean {
  if (['/', '/apps', '/connections', '/connections/providers', '/connections/mcp', '/security', '/security/identity', '/security/backups', '/system', '/system/diagnostics'].includes(path)) return true;
  const match = path.match(/^\/apps\/([^/]+)$/);
  return Boolean(match && services.some(service => service.id === match[1]));
}
function navigate(path: string) { window.history.pushState({}, '', path); window.dispatchEvent(new PopStateEvent('popstate')); }

export default function App() {
  const [locationPath, setLocationPath] = useState(() => window.location.pathname); const [tab, setTab] = useState<Tab>(() => routeTab(window.location.pathname)); const [data, setData] = useState<DashboardData | null>(null); const [error, setError] = useState('');
  useEffect(() => { const sync = () => { setLocationPath(window.location.pathname); setTab(routeTab(window.location.pathname)); }; window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync); }, []);
  useEffect(() => {
    let active = true;
    const load = () => Promise.all([api<Health>('/api/health'), api<ServicesResponse>('/api/services'), api<CatalogResponse>('/api/catalog'), api<SystemResponse>('/api/system'), api<IntegrationsResponse>('/api/integrations'), api<IdentityResponse>('/api/identity'), api<JobsResponse>('/api/jobs'), api<AuditResponse>('/api/audit'), api<CoreSetupResponse>('/api/setup/core'), api<ProvisioningResponse>('/api/provisioning')]).then(([health, services, catalog, system, integrations, identity, jobs, audit, core, provisioning]) => {
      if (active) { setData({ health, services, catalog, system, integrations, identity, jobs, audit, core, provisioning }); setError(''); }
    }).catch((reason: unknown) => active && setError(reason instanceof Error ? reason.message : String(reason)));
    load(); const timer = window.setInterval(load, 10000); return () => { active = false; window.clearInterval(timer); };
  }, []);
  const content = !data ? <div className="loading">Connecting to the Mu3Lab control plane…</div> : !knownRoute(locationPath, data.services.services) ? <section className="panel"><p className="eyebrow">NOT FOUND</p><h2>This dashboard page does not exist</h2><p>Use the sidebar to return to a supported Mu3Lab area.</p><a className="primary-action" href="/" onClick={event => { event.preventDefault(); navigate('/'); }}>Return home →</a></section> : (() => {
    if (tab === 'home') return <HomePanel services={data.services.services} system={data.system} identity={data.identity} jobs={data.jobs} core={data.core} provisioning={data.provisioning} />;
    if (tab === 'apps') return <AppsPanel key={locationPath} services={data.services.services} catalog={data.catalog} />;
    if (tab === 'connections') return <AiMcpPanel integrations={data.integrations} services={data.services.services} />;
    if (tab === 'security') return <ProtectionPanel identity={data.identity} backup={data.system.backup} audit={data.audit} />;
    return <SystemPanel system={data.system} services={data.services.services} jobs={data.jobs} />;
  })();
  return <div className="app-shell"><header className="app-header"><a className="brand" href="/" onClick={event => { event.preventDefault(); navigate('/'); }}><b>μ</b><span>Mu3Lab<small>Private app platform</small></span></a><div className="host-chip">{data?.system.tailnet_dns_name || 'Tailnet checking…'}</div><div className="header-spacer" /><div className={`connection ${data?.health.ok ? 'good' : ''}`}>{data?.health.ok ? 'Control plane online' : 'Connecting…'}</div></header><div className="app-body"><nav className="sidebar" aria-label="Mu3Lab navigation">{tabs.map((item, index) => <div key={item.id}>{item.group && <span className={index ? 'nav-group separated' : 'nav-group'}>{item.group}</span>}<button className={tab === item.id ? 'active' : ''} onClick={() => navigate(item.path)} aria-current={tab === item.id ? 'page' : undefined}>{item.label}</button></div>)}</nav><main className="page"><div className="page-heading"><div><p className="eyebrow">PRIVATE BY DEFAULT</p><h1>{tabs.find(item => item.id === tab)?.label}</h1></div>{data && <span className={data.identity.writes_enabled ? 'write-state enabled' : 'write-state'}>{data.identity.writes_enabled ? 'Changes protected' : 'Read-only until identity is configured'}</span>}</div>{error && <div className="error" role="alert">Dashboard data is unavailable: {error}</div>}{content}</main></div></div>;
}
