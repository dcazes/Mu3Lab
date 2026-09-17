import { useEffect, useState } from 'react';
import { api, AuditResponse, CatalogResponse, Health, IdentityResponse, IntegrationsResponse, JobsResponse, ServicesResponse, SystemResponse } from './api';
import { AiMcpPanel, AppsPanel, HomePanel, ProtectionPanel, SystemPanel } from './panels';

type Tab = 'home' | 'apps' | 'ai-mcp' | 'protection' | 'system';
const tabs: { id: Tab; label: string; group?: string }[] = [
  { id: 'home', label: 'Home', group: 'Platform' }, { id: 'apps', label: 'Apps' }, { id: 'ai-mcp', label: 'AI & MCP' },
  { id: 'protection', label: 'Identity & backup', group: 'Protection' }, { id: 'system', label: 'System', group: 'Host' },
];
type DashboardData = { health: Health; services: ServicesResponse; catalog: CatalogResponse; system: SystemResponse; integrations: IntegrationsResponse; identity: IdentityResponse; jobs: JobsResponse; audit: AuditResponse };

export default function App() {
  const [tab, setTab] = useState<Tab>('home'); const [data, setData] = useState<DashboardData | null>(null); const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const load = () => Promise.all([api<Health>('/api/health'), api<ServicesResponse>('/api/services'), api<CatalogResponse>('/api/catalog'), api<SystemResponse>('/api/system'), api<IntegrationsResponse>('/api/integrations'), api<IdentityResponse>('/api/identity'), api<JobsResponse>('/api/jobs'), api<AuditResponse>('/api/audit')]).then(([health, services, catalog, system, integrations, identity, jobs, audit]) => {
      if (active) { setData({ health, services, catalog, system, integrations, identity, jobs, audit }); setError(''); }
    }).catch((reason: unknown) => active && setError(reason instanceof Error ? reason.message : String(reason)));
    load(); const timer = window.setInterval(load, 10000); return () => { active = false; window.clearInterval(timer); };
  }, []);
  const content = !data ? <div className="loading">Connecting to the Mu3Lab control plane…</div> : (() => {
    if (tab === 'home') return <HomePanel services={data.services.services} system={data.system} identity={data.identity} jobs={data.jobs} />;
    if (tab === 'apps') return <AppsPanel services={data.services.services} catalog={data.catalog} />;
    if (tab === 'ai-mcp') return <AiMcpPanel integrations={data.integrations} services={data.services.services} />;
    if (tab === 'protection') return <ProtectionPanel identity={data.identity} backup={data.system.backup} audit={data.audit} />;
    return <SystemPanel system={data.system} services={data.services.services} jobs={data.jobs} />;
  })();
  return <div className="app-shell"><header className="app-header"><a className="brand" href="/"><b>μ</b><span>Mu3Lab<small>Private app platform</small></span></a><div className="host-chip">{data?.system.tailnet_dns_name || 'Tailnet checking…'}</div><div className="header-spacer" /><div className={`connection ${data?.health.ok ? 'good' : ''}`}>{data?.health.ok ? 'Control plane online' : 'Connecting…'}</div></header><div className="app-body"><nav className="sidebar" aria-label="Mu3Lab navigation">{tabs.map((item, index) => <div key={item.id}>{item.group && <span className={index ? 'nav-group separated' : 'nav-group'}>{item.group}</span>}<button className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)} aria-current={tab === item.id ? 'page' : undefined}>{item.label}</button></div>)}</nav><main className="page"><div className="page-heading"><div><p className="eyebrow">PRIVATE BY DEFAULT</p><h1>{tabs.find(item => item.id === tab)?.label}</h1></div>{data && <span className={data.identity.writes_enabled ? 'write-state enabled' : 'write-state'}>{data.identity.writes_enabled ? 'Changes protected' : 'Read-only until identity is configured'}</span>}</div>{error && <div className="error" role="alert">Dashboard data is unavailable: {error}</div>}{content}</main></div></div>;
}
