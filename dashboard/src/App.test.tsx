import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ChatPanel } from './App';
import type { ChatStatus } from './api';

const status: ChatStatus = {
  ok: true,
  ready: true,
  url: 'https://lobe.example',
  authentication: 'native_oidc',
  mcp_enabled_count: 0,
  detail: 'Chat is ready.',
  providers: [
    { id: 'lobehub', name: 'LobeChat', ready: true, url: 'https://lobe.example', authentication: 'native_oidc', detail: 'Ready.' },
  ],
};

describe('primary chat', () => {
  afterEach(cleanup);

  it('opens the verified LobeChat route', () => {
    render(<ChatPanel status={status} services={[]} />);
    expect(screen.getByTitle('Mu3Lab LobeChat chat')).toHaveAttribute('src', 'https://lobe.example');
    expect(screen.getByRole('link', { name: 'Open LobeChat ↗' })).toHaveAttribute('href', 'https://lobe.example');
  });
});
