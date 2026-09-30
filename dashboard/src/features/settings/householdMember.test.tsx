import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { AiSettings } from './AiSettings';
import { SettingsPage } from './SettingsPage';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const member = () => {
  const data = dashboardData([]);
  return { ...data, identity: { ...data.identity, is_admin: false, role: 'member' as const } };
};

describe('household member', () => {
  it('does not see the System or People settings', () => {
    stubFetch(() => ({ ok: true, providers: [] }));
    renderWithDashboard(<SettingsPage section="system" />, member());
    const nav = screen.getByRole('navigation', { name: 'Settings' });
    expect(nav).not.toHaveTextContent('System');
    expect(nav).not.toHaveTextContent('People');
    expect(nav).toHaveTextContent('AI providers');
    expect(screen.getByText('Settings page not found')).toBeInTheDocument();
  });

  it('sees AI providers without being able to change them', async () => {
    stubFetch((path) =>
      path === '/api/v1/providers'
        ? {
            ok: true,
            providers: [
              {
                id: 'groq',
                name: 'Groq',
                label: 'Groq',
                state: 'verified',
                enabled: true,
                supported: true,
                model_samples: [],
                credential_indicator: '••••',
                updated_at: '',
              },
            ],
            setup: { complete: true },
          }
        : { ok: true, providers: [] },
    );
    renderWithDashboard(<AiSettings />, member());
    expect(await screen.findByText('Only an administrator can change AI providers')).toBeInTheDocument();
    expect(await screen.findByText('Groq')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Groq actions' })).not.toBeInTheDocument();
  });
});
