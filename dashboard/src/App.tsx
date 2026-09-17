/**
 * Mu3Lab :: dashboard/src/App.tsx
 * WHAT: Status screen for installed infrastructure. Fetches /api/health and
 *       /api/status, renders one card per component. Deliberately small:
 *       Authentik/Vaultwarden/app UI arrives in later phases.
 * WHY:  The first thing a user sees on :8787 must answer "what's running?"
 *       with zero interaction. No router yet — one page is enough.
 * DEBUG: Failed fetch shows the error inline (backend down?) instead of blank.
 */
import { useEffect, useState } from 'react';
import { api, Health, IntegrationsResponse, ServicesResponse } from './api';

const stateColor: Record<string, string> = {
  healthy: '#1a7f37',
  blocked: '#8250df',
  not_installed: '#9a6700',
  stopped_or_unhealthy: '#cf222e',
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [services, setServices] = useState<ServicesResponse | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationsResponse | null>(null);
  const [error, setError] = useState<string>('');

  useEffect(() => {
    Promise.all([
      api<Health>('/api/health'),
      api<ServicesResponse>('/api/services'),
      api<IntegrationsResponse>('/api/integrations'),
    ])
      .then(([h, s, i]) => {
        setHealth(h);
        setServices(s);
        setIntegrations(i);
      })
      .catch((e) => setError(String(e.message || e)));
  }, []);

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', maxWidth: 720, margin: '2rem auto', padding: '0 1rem' }}>
      <h1>Mu3Lab</h1>
      {error && <p style={{ color: '#cf222e' }}>Backend unreachable: {error}</p>}
      {health && (
        <p>
          Control plane: {health.ok ? '✓ running' : '✗ unhealthy'}{' '}
          <span style={{ color: '#57606a' }}>v{health.version}</span>
        </p>
      )}
      {services && (
        <section>
          <h2>Curated services</h2>
          {services.tailnet_dns_name ? (
            <p>Private entrypoint: <code>https://{services.tailnet_dns_name}</code></p>
          ) : (
            <p style={{ color: '#9a6700' }}>Tailscale is not connected yet. Complete bootstrap before opening apps remotely.</p>
          )}
          <div style={{ display: 'grid', gap: '0.75rem' }}>
            {services.services.map((service) => (
              <article key={service.id} style={{ border: '1px solid #d0d7de', borderRadius: 8, padding: '0.9rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'baseline' }}>
                  <strong>{service.name}</strong>
                  <span style={{ color: stateColor[service.state] }}>{service.state.replace(/_/g, ' ')}</span>
                </div>
                <p style={{ margin: '0.4rem 0' }}>{service.detail}</p>
                <small>
                  {service.auth === 'excluded' ? 'No browser/SSO exposure' : `Auth: ${service.auth}`} · {service.lifecycle.replace(/_/g, ' ')}
                  {service.mcp.exposed ? ` · MCP: ${service.mcp.risk}` : ''}
                </small>
                {service.url && service.state === 'healthy' && (
                  <div style={{ marginTop: '0.5rem' }}><a href={service.url}>Open securely</a></div>
                )}
                {service.state === 'blocked' && <p style={{ color: '#8250df', marginBottom: 0 }}>{service.blocked_reason}</p>}
              </article>
            ))}
          </div>
        </section>
      )}
      {integrations && (
        <section>
          <h2>AI routing</h2>
          <p>Policy: <strong>{integrations.policy}</strong> — paid routes are never selected automatically.</p>
          <ul>{integrations.integrations.map((item) => <li key={`${item.source}-${item.destination}`}>{item.source} → {item.destination} <span style={{ color: '#57606a' }}>({item.kind.replace(/_/g, ' ')})</span></li>)}</ul>
        </section>
      )}
    </main>
  );
}
