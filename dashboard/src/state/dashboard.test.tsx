import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { dashboardData } from '../test/fixtures';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Dashboard connection failures', () => {
  it('reports a snapshot server error and recovers when retry succeeds', async () => {
    let failing = true;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) => {
        if (path === '/api/v1/snapshot' && failing) return new Response('Internal Server Error', { status: 500 });
        return new Response(JSON.stringify(path === '/api/v1/snapshot' ? dashboardData([]) : { ok: true }));
      }),
    );
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Mu3Lab could not load your dashboard' })).toBeInTheDocument();
    expect(screen.getByText(/HTTP 500/)).toBeInTheDocument();
    expect(screen.queryByText(/Make sure Tailscale/)).not.toBeInTheDocument();
    failing = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { name: /Good/ })).toBeInTheDocument();
  });

  it('keeps connection advice for a network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Can’t reach Mu3Lab' })).toBeInTheDocument();
    expect(screen.getByText(/Make sure Tailscale/)).toBeInTheDocument();
  });

  it('requires sign-in when the data request redirects to Authentik', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) =>
        path === '/api/v1/snapshot' ? new Response(null, { status: 302 }) : new Response(JSON.stringify({ ok: true })),
      ),
    );
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Your session ended' })).toBeInTheDocument();
  });
});
