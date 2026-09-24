import { useEffect, useState } from 'react';
import { api, AuditResponse, CatalogResponse, ChatStatus, CoreSetupResponse, Health, IdentityResponse, IntegrationsResponse, JobsResponse, ProvisioningResponse, ServicesResponse, SystemResponse } from './api';
import { AiMcpPanel, AppsPanel, HomePanel, ProtectionPanel, SystemPanel } from './panels';

type Tab = 'home' | 'apps' | 'chat' | 'connections' | 'security' | 'system';
const tabs: { id: Tab; label: string; group?: string; path: string }[] = [
  { id: 'home', label: 'Home', group: 'Platform', path: '/' }, { id: 'apps', label: 'My Apps', path: '/apps' },
  { id: 'chat', label: 'Chat', path: '/chat' },
  { id: 'connections', label: 'Connections', path: '/connections' },
  { id: 'security', label: 'Security & Backups', group: 'Protection', path: '/security' },
  { id: 'system', label: 'System', group: 'Host', path: '/system' },
];
type DashboardData = { health: Health; services: ServicesResponse; catalog: CatalogResponse; system: SystemResponse; integrations: IntegrationsResponse; identity: IdentityResponse; jobs: JobsResponse; audit: AuditResponse; core: CoreSetupResponse; provisioning: ProvisioningResponse; chat: ChatStatus };
const emptyData: DashboardData = {
  health: { ok: false, version: '' }, services: { ok: false, tailnet_dns_name: '', services: [] }, catalog: { ok: false, profiles: [], services: {} },
  system: { ok: false, cpu_percent: 0, uptime_seconds: 0, docker_ready: false, tailnet_dns_name: '', runtime_root: '', memory: { total: 0, used: 0, percent: 0 }, disk: { total: 0, used: 0, percent: 0 }, backup: {} },
  integrations: { ok: false, policy: '', integrations: [] }, identity: { ok: false, control_plane_auth: 'not_configured', detail: 'Identity status unavailable.', writes_enabled: false },
  jobs: { ok: false, available: false, jobs: [] }, audit: { ok: false, available: false, events: [] },
  core: { ok: false, ready_to_run: false, services: [], missing_manifests: [], next_action: 'Core status unavailable.' }, provisioning: { ok: false, available: false, complete: false, phases: [] },
  chat: { ok: false, ready: false, url: '', authentication: 'native_oidc', mcp_enabled_count: 0, detail: 'Chat status unavailable.' },
};
function routeTab(path: string): Tab { if (path === '/' || path === '') return 'home'; if (path.startsWith('/apps')) return 'apps'; if (path.startsWith('/chat')) return 'chat'; if (path.startsWith('/connections')) return 'connections'; if (path.startsWith('/security')) return 'security'; if (path.startsWith('/system')) return 'system'; return 'home'; }
function knownRoute(path: string, services: { id: string }[]): boolean {
  if (['/', '/apps', '/chat', '/connections', '/connections/providers', '/connections/mcp', '/security', '/security/identity', '/security/backups', '/system', '/system/diagnostics'].includes(path)) return true;
  const match = path.match(/^\/apps\/([^/]+)$/);
  return Boolean(match && services.some(service => service.id === match[1]));
}
function navigate(path: string) { window.history.pushState({}, '', path); window.dispatchEvent(new PopStateEvent('popstate')); }
function expectedMcp(): { name: string; serviceId: string } | null {
  try {
    const value = JSON.parse(window.sessionStorage.getItem('mu3lab.expectedMcp') || 'null');
    return value && typeof value.name === 'string' && typeof value.serviceId === 'string' ? value : null;
  } catch { return null; }
}

export function ChatPanel({ status, services }: { status: ChatStatus; services: ServicesResponse['services'] }) {
  const expected = expectedMcp();
  const parent = expected ? services.find(service => service.id === expected.serviceId) : undefined;
  return <div className="chat-page">
    <header className="chat-provider-bar">
      <div>LobeChat</div>
      {status.ready && status.url && <a href={status.url} target="_blank" rel="noreferrer">Open LobeChat ↗</a>}
    </header>
    {expected && <div className="chat-context"><span><b>{expected.name}</b> is expected to be available in this chat.</span>{parent?.route_ready && <a href={parent.url} target="_blank" rel="noreferrer">Open {parent.name} ↗</a>}</div>}
    {status.ready && status.url ? <section className="chat-shell"><iframe title="Mu3Lab LobeChat chat" src={status.url} allow="clipboard-read; clipboard-write" /></section> : <section className="panel chat-unavailable"><p className="eyebrow">CHAT</p><h2>LobeChat is not ready</h2><p>{status.detail}</p><a className="button button-primary" href="/system">Check core status →</a></section>}
  </div>;
}

export default function App() {
  const [locationPath, setLocationPath] = useState(() => window.location.pathname); const [tab, setTab] = useState<Tab>(() => routeTab(window.location.pathname)); const [data, setData] = useState<DashboardData | null>(null); const [error, setError] = useState('');
  useEffect(() => { const sync = () => { setLocationPath(window.location.pathname); setTab(routeTab(window.location.pathname)); }; window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync); }, []);
  useEffect(() => {
    let active = true; let controller: AbortController | null = null;
    const load = async () => { if (controller) return; controller = new AbortController(); const signal = controller.signal; const results = await Promise.allSettled([api<Health>('/api/health', signal), api<ServicesResponse>('/api/services', signal), api<CatalogResponse>('/api/catalog', signal), api<SystemResponse>('/api/system', signal), api<IntegrationsResponse>('/api/integrations', signal), api<IdentityResponse>('/api/identity', signal), api<JobsResponse>('/api/jobs', signal), api<AuditResponse>('/api/audit', signal), api<CoreSetupResponse>('/api/setup/core', signal), api<ProvisioningResponse>('/api/provisioning', signal), api<ChatStatus>('/api/v1/chat/status', signal)]); controller = null; if (!active) return; setData(previous => { const base = previous || emptyData; return { health: results[0].status === 'fulfilled' ? results[0].value : base.health, services: results[1].status === 'fulfilled' ? results[1].value : base.services, catalog: results[2].status === 'fulfilled' ? results[2].value : base.catalog, system: results[3].status === 'fulfilled' ? results[3].value : base.system, integrations: results[4].status === 'fulfilled' ? results[4].value : base.integrations, identity: results[5].status === 'fulfilled' ? results[5].value : base.identity, jobs: results[6].status === 'fulfilled' ? results[6].value : base.jobs, audit: results[7].status === 'fulfilled' ? results[7].value : base.audit, core: results[8].status === 'fulfilled' ? results[8].value : base.core, provisioning: results[9].status === 'fulfilled' ? results[9].value : base.provisioning, chat: results[10].status === 'fulfilled' ? results[10].value : base.chat }; }); const failures = results.filter(result => result.status === 'rejected'); setError(failures.length ? `${failures.length} dashboard data source${failures.length === 1 ? '' : 's'} unavailable; healthy sections remain usable.` : ''); };
    load(); const timer = window.setInterval(load, 10000); return () => { active = false; controller?.abort(); window.clearInterval(timer); };
  }, []);
  const content = !data ? <div className="loading">Connecting to the Mu3Lab control plane…</div> : !knownRoute(locationPath, data.services.services) ? <section className="panel"><p className="eyebrow">NOT FOUND</p><h2>This dashboard page does not exist</h2><p>Use the sidebar to return to a supported Mu3Lab area.</p><a className="primary-action" href="/" onClick={event => { event.preventDefault(); navigate('/'); }}>Return home →</a></section> : (() => {
    if (tab === 'home') return <HomePanel services={data.services.services} system={data.system} jobs={data.jobs} />;
    if (tab === 'apps') return <AppsPanel key={locationPath} services={data.services.services} catalog={data.catalog} />;
    if (tab === 'chat') {
      return <ChatPanel status={data.chat} services={data.services.services} />;
    }
    if (tab === 'connections') return <AiMcpPanel integrations={data.integrations} services={data.services.services} />;
    if (tab === 'security') return <ProtectionPanel identity={data.identity} backup={data.system.backup} audit={data.audit} />;
    return <SystemPanel system={data.system} services={data.services.services} jobs={data.jobs} core={data.core} provisioning={data.provisioning} identity={data.identity} />;
  })();
  return <div className="app-shell"><header className="app-header"><a className="brand" href="/" onClick={event => { event.preventDefault(); navigate('/'); }}><b>μ</b><span>Mu3Lab<small>Private app platform</small></span></a><div className="host-chip">{data?.system.tailnet_dns_name || 'Tailnet checking…'}</div><div className="header-spacer" /><div className={`connection ${data?.health.ok ? 'good' : ''}`}>{data?.health.ok ? 'Control plane online' : 'Connecting…'}</div></header><div className="app-body"><nav className="sidebar" aria-label="Mu3Lab navigation">{tabs.map((item, index) => <div key={item.id}>{item.group && <span className={index ? 'nav-group separated' : 'nav-group'}>{item.group}</span>}<button className={tab === item.id ? 'active' : ''} onClick={() => navigate(item.path)} aria-current={tab === item.id ? 'page' : undefined}>{item.label}</button></div>)}</nav><main className="page"><div className="page-heading"><div><p className="eyebrow">PRIVATE BY DEFAULT</p><h1>{tabs.find(item => item.id === tab)?.label}</h1></div>{data && <span className={data.identity.writes_enabled ? 'write-state enabled' : 'write-state'}>{data.identity.writes_enabled ? 'Changes protected' : 'Read-only until identity is configured'}</span>}</div>{error && <div className="error" role="alert">Dashboard data is unavailable: {error}</div>}{content}</main></div></div>;
}
