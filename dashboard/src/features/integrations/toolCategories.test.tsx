import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { McpServer } from '../../api';
import { connectorsFor, primaryConnectors } from './mcp';
import { ToolCategories } from './ToolCategories';

const putJsonApi = vi.fn((_path: string, _body: unknown) => Promise.resolve({ ok: true }));
vi.mock('../../api', async (original) => ({
  ...(await original<typeof import('../../api')>()),
  putJsonApi: (path: string, body: unknown) => putJsonApi(path, body),
}));

afterEach(() => {
  cleanup();
  putJsonApi.mockClear();
});

const server = (overrides: Partial<McpServer> = {}): McpServer => ({
  id: 'immich-photo-manager',
  name: 'Immich Photo Manager',
  service_id: 'immich',
  kind: 'community',
  transport: 'streamable-http',
  app_state: 'ready',
  enabled: true,
  state: 'live',
  auth: { type: 'service-credential', configured: true, auto_provision: true },
  review: { status: 'accepted', repository: '', revision: '', preferred: true },
  gateway: true,
  categories: [
    { id: 'search', title: 'Search and details', summary: 'Find photos.', enabled: true, default_on: true },
    { id: 'trash', title: 'Trash', summary: 'Move photos to the trash.', enabled: false, default_on: false },
  ],
  tools: [
    { id: 'search_smart', title: 'Search by content', risk: 'read', enabled: true, category: 'search', core: true },
    { id: 'delete_assets', title: 'Delete photos', risk: 'write', enabled: false, category: 'trash' },
  ],
  blocked: [{ id: 'update_credentials', reason: 'Lets a chat change the API key.' }],
  ...overrides,
});

describe('ToolCategories', () => {
  it('shows every category with its state, and tools on demand', () => {
    render(<ToolCategories server={server()} reload={vi.fn()} />);
    expect(screen.getByText('1 of 1 on')).toBeInTheDocument();
    expect(screen.getByText('Off')).toBeInTheDocument();
    expect(screen.queryByRole('switch', { name: 'Search smart' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Search and details/ }));
    expect(screen.getByRole('switch', { name: 'Search smart' })).toBeChecked();
    expect(screen.getByText('Everyday')).toBeInTheDocument();
  });

  it('switches a category on through the API', async () => {
    const reload = vi.fn();
    render(<ToolCategories server={server()} reload={reload} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Trash' }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(putJsonApi).toHaveBeenCalledWith('/api/v1/mcp/servers/immich-photo-manager/categories/trash', {
      enabled: true,
    });
  });

  it('keeps writes unavailable even when their saved permission is enabled', () => {
    const trashOn = server({
      categories: server().categories!.map((category) => ({ ...category, enabled: true })),
      tools: server().tools.map((tool) => ({ ...tool, enabled: true })),
    });
    render(<ToolCategories server={trashOn} reload={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Trash/ }));
    expect(screen.getByText('Unavailable in chat')).toBeInTheDocument();
    const writeSwitch = screen.getByRole('switch', { name: 'Delete assets' });
    expect(writeSwitch).toBeDisabled();
    expect(writeSwitch).not.toBeChecked();
    expect(putJsonApi).not.toHaveBeenCalled();
  });

  it('keeps tools in a switched-off category from being switched on', () => {
    render(<ToolCategories server={server()} reload={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Trash/ }));
    expect(screen.getByRole('switch', { name: 'Delete assets' })).toBeDisabled();
  });
});

describe('connector selection', () => {
  const photoManager = server({ enabled: false });
  const control = server({
    id: 'immich-control',
    name: 'Immich Control',
    enabled: true,
    review: { status: 'accepted', repository: '', revision: '', preferred: false },
  });

  it('puts the connector in use first, then the default', () => {
    expect(connectorsFor([photoManager, control], 'immich').map((item) => item.id)).toEqual([
      'immich-control',
      'immich-photo-manager',
    ]);
    expect(
      connectorsFor(
        [control, photoManager].map((item) => ({ ...item, enabled: false })),
        'immich',
      )[0].id,
    ).toBe('immich-photo-manager');
  });

  it('lists each app once', () => {
    expect(primaryConnectors([photoManager, control]).map((item) => item.id)).toEqual(['immich-control']);
  });
});
