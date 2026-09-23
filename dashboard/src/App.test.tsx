import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ChatPanel, HomarrPanel } from './App';
import type { ChatStatus, Service } from './api';

const status: ChatStatus = {
  ok: true,
  ready: true,
  url: 'https://open-webui.example',
  authentication: 'trusted_header',
  mcp_enabled_count: 0,
  detail: 'Chat is ready.',
  providers: [
    { id: 'lobehub', name: 'LobeChat', ready: true, url: 'https://lobe.example', authentication: 'native_oidc', detail: 'Ready.' },
    { id: 'open-webui', name: 'Open WebUI', ready: true, url: 'https://open-webui.example', authentication: 'trusted_header', detail: 'Ready.' },
  ],
};

describe('chat provider tabs', () => {
  afterEach(cleanup);

  it('prefers LobeChat while preserving Open WebUI as a selectable chat', () => {
    render(<ChatPanel status={status} services={[]} />);
    expect(screen.getByRole('tab', { name: /LobeChat Ready/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTitle('Mu3Lab LobeChat chat')).toHaveAttribute('src', 'https://lobe.example');

    fireEvent.click(screen.getByRole('tab', { name: /Open WebUI Ready/ }));
    expect(screen.getByTitle('Mu3Lab Open WebUI chat')).toHaveAttribute('src', 'https://open-webui.example');
    expect(screen.getByRole('link', { name: 'Open Open WebUI ↗' })).toHaveAttribute('href', 'https://open-webui.example');
  });
});

describe('Homarr alternate dashboard', () => {
  afterEach(cleanup);

  it('embeds the verified private route and offers a full-page link', () => {
    const service = { id: 'homarr', name: 'Homarr', state: 'ready', route_ready: true, url: 'https://mu3lab.example.ts.net:8458', identity: { mode: 'local' } } as Service;
    render(<HomarrPanel service={service} />);
    expect(screen.getByTitle('Mu3Lab Homarr dashboard')).toHaveAttribute('src', service.url);
    expect(screen.getByRole('link', { name: 'Open full page ↗' })).toHaveAttribute('href', service.url);
    expect(screen.getByText(/Native application status, stack controls/)).toBeInTheDocument();
  });

  it('keeps the dashboard embedded and explains the one-time top-level OIDC login', () => {
    const service = { id: 'homarr', name: 'Homarr', state: 'ready', route_ready: true, url: 'https://mu3lab.example.ts.net:8458', identity: { mode: 'native_oidc' } } as Service;
    render(<HomarrPanel service={service} />);
    expect(screen.getByTitle('Mu3Lab Homarr dashboard')).toHaveAttribute('src', service.url);
    expect(screen.getByText(/Open Homarr full page to complete Authentik sign-in/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open full page ↗' })).toHaveAttribute('href', service.url);
  });

  it('keeps installation inside the existing app-management flow', () => {
    const service = { id: 'homarr', name: 'Homarr', state: 'not_installed', route_ready: false, url: '', detail: 'Not installed.' } as Service;
    render(<HomarrPanel service={service} />);
    expect(screen.getByRole('heading', { name: 'Install the alternate dashboard' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Manage Homarr →' })).toHaveAttribute('href', '/apps/homarr');
  });
});
