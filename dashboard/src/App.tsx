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
import { api, Health, InfraStatus } from './api';

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [status, setStatus] = useState<InfraStatus | null>(null);
  const [error, setError] = useState<string>('');

  useEffect(() => {
    Promise.all([api<Health>('/api/health'), api<InfraStatus>('/api/status')])
      .then(([h, s]) => {
        setHealth(h);
        setStatus(s);
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
      {status && (
        <section>
          <h2>Infrastructure</h2>
          <ul>
            {status.components.map((c) => (
              <li key={c.id}>
                <strong>{c.label}</strong> — {c.detail}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
