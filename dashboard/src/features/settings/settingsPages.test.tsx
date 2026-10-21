import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { McpServer } from '../../api';
import { dashboardData, renderWithDashboard, service, stubFetch } from '../../test/fixtures';
import { IntegrationsSettings } from './IntegrationsSettings';
import { WhatsNew } from './WhatsNew';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("What's new", () => {
  it('folds each release into its developments, the installed one open', async () => {
    stubFetch((path) => {
      if (path === '/api/v1/system/update') return { ok: true, available: false, behind: 0, changes: [] };
      if (path === '/api/v1/system/changelog')
        return {
          ok: true,
          releases: [
            {
              version: '1.1.0',
              date: '2026-10-20',
              changes: ['feat(livekit): voice assistant app', 'fix(rules): skip account sync'],
              developments: [
                {
                  title: 'Livekit voice',
                  branch: 'feature/livekit-voice',
                  date: '2026-10-20',
                  changes: ['feat(livekit): voice assistant app'],
                },
                {
                  title: 'Stopped-app account sync',
                  branch: '',
                  date: '2026-10-18',
                  changes: ['fix(rules): skip account sync'],
                },
              ],
            },
            { version: '1.0.0', date: '2026-10-01', changes: ['first'], developments: [] },
          ],
        };
      return undefined;
    });
    renderWithDashboard(<WhatsNew />, dashboardData([]));
    const installed = await screen.findByRole('button', { name: /Version 1.1.0 \(installed\)/ });
    expect(installed).toHaveAttribute('aria-expanded', 'true');
    // Older releases stay folded until opened.
    const older = screen.getByRole('button', { name: /Version 1.0.0/ });
    expect(older).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('first')).not.toBeInTheDocument();
    // Each development is its own fold with its branch, date and size; the newest starts open.
    const voice = screen.getByText('Livekit voice').closest('details')!;
    expect(voice).toHaveAttribute('open');
    expect(within(voice).getByText('feature/livekit-voice · 2026-10-20 · 1 change')).toBeInTheDocument();
    expect(screen.getByText('Stopped-app account sync').closest('details')).not.toHaveAttribute('open');
    fireEvent.click(older);
    expect(screen.getByText('first')).toBeInTheDocument();
  });
});

function connector(serviceId: string, name: string, overrides: Partial<McpServer> = {}): McpServer {
  return {
    id: `${serviceId}-mcp`,
    service_id: serviceId,
    name,
    kind: 'official',
    transport: 'http',
    app_state: 'running',
    enabled: true,
    state: 'live',
    tools: [],
    auth: { auto_provision: true, configured: true, type: 'api_key' },
    ...overrides,
  } as McpServer;
}

describe('Chat integrations', () => {
  it('shows each app’s assistant in its own row, not in a card of its own', async () => {
    stubFetch((path) =>
      path === '/api/v1/mcp/servers'
        ? { ok: true, servers: [connector('grocy', 'Grocy'), connector('mealie', 'Mealie')], policy: '' }
        : undefined,
    );
    const data = dashboardData([service('grocy', 'Grocy', 'optional'), service('mealie', 'Mealie', 'optional')]);
    data.chat = {
      ...data.chat,
      assistants: [
        { id: 'grocy', name: 'Grocy', status: 'ready', detail: 'Available in chat.' },
        {
          id: 'mealie',
          name: 'Mealie',
          status: 'pending',
          detail: 'Adding to chat and checking it within five minutes.',
        },
      ],
    };
    renderWithDashboard(<IntegrationsSettings />, data);
    const automatic = await screen.findByRole('region', { name: 'Set up automatically' });
    expect(screen.queryByText('Assistants in chat')).not.toBeInTheDocument();
    // The count and the tools link moved into the card below.
    expect(screen.getByText(/1 of 2 apps have an assistant in chat/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Manage tools' })).toHaveAttribute('href', '/apps/contextforge');
    // Only an assistant that is not ready yet adds a line, to its own app.
    expect(
      within(automatic).getByText('Assistant: Adding to chat and checking it within five minutes.'),
    ).toBeInTheDocument();
    expect(within(automatic).queryByText(/Assistant: Available/)).not.toBeInTheDocument();
  });
});
