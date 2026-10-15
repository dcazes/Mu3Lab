import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { GetStarted, GetStartedSettings } from './GetStarted';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function serve(items: Record<string, boolean>) {
  return stubFetch((path, init) => {
    if (path === '/api/v1/me/checklist') {
      if (init?.method === 'PUT') items = { ...items, ...JSON.parse(String(init.body)).items };
      return { ok: true, items };
    }
    if (path === '/api/v1/providers') return { ok: true, providers: [], setup: { complete: false } };
    if (path === '/api/v1/vault/status') return { ok: true, seeded: true, browser_extension: null };
    return undefined;
  });
}

describe('Get started', () => {
  it('leaves a small button on Home that brings a hidden list back', async () => {
    const fetchMock = serve({ hidden: true });
    renderWithDashboard(<GetStarted />, dashboardData([]));
    fireEvent.click(await screen.findByRole('button', { name: /Show Get started · 0 of 5 complete/ }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/me/checklist',
        expect.objectContaining({ method: 'PUT', body: JSON.stringify({ items: { hidden: false } }) }),
      ),
    );
    expect(await screen.findByRole('heading', { name: 'Get started' })).toBeInTheDocument();
  });

  it('has its own Settings page with a Show on Home switch', async () => {
    serve({ hidden: true });
    renderWithDashboard(<GetStartedSettings />, dashboardData([]));
    const toggle = await screen.findByRole('switch', { name: 'Show on Home' });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    expect(await screen.findByRole('switch', { name: 'Show on Home' })).toBeChecked();
  });
});
