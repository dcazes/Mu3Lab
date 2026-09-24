import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ChatPanel } from './App';
import type { ChatStatus } from './api';

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
