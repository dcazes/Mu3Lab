import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MobileClient } from '../../api';
import { service } from '../../test/fixtures';
import { DevicesSection } from './DevicesSection';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const client = (overrides: Partial<MobileClient>): MobileClient => ({
  id: 'immich-mobile',
  name: 'Immich',
  kind: 'native',
  support: 'official',
  platforms: ['ios', 'android'],
  install: { ios: 'https://apps.apple.com/app/immich', android: 'https://play.google.com/store/apps/immich' },
  setup: 'server_url',
  summary: 'Photo backup.',
  steps: ['Install Immich and paste the server URL.'],
  homepage: '',
  source: '',
  caveat: '',
  fallback: false,
  ...overrides,
});

const immich = (clients: MobileClient[]) =>
  service('immich', 'Immich', 'optional', { mobile: { primary: clients[0].id, clients } });

describe('DevicesSection', () => {
  it('puts the store for this phone first', () => {
    vi.stubGlobal('navigator', { ...navigator, userAgent: 'Mozilla/5.0 (Linux; Android 14)', maxTouchPoints: 5 });
    render(<DevicesSection service={immich([client({})])} address="https://host.ts.net:8449" />);
    const stores = screen.getAllByRole('link', { name: /Google Play|App Store/ }).map((link) => link.textContent);
    expect(stores[0]).toContain('Google Play');
    expect(
      screen.getByText(
        'This is the server address the Immich app asks for. Copy it, or scan the QR code with your phone.',
      ),
    ).toBeInTheDocument();
  });

  it('offers a QR code of the store link on a computer', async () => {
    vi.stubGlobal('navigator', { ...navigator, userAgent: 'Mozilla/5.0 (X11; Linux x86_64)', maxTouchPoints: 0 });
    render(<DevicesSection service={immich([client({})])} address="https://host.ts.net:8449" />);
    fireEvent.click(screen.getByRole('button', { name: 'Show a QR code for Immich on App Store' }));
    expect(await screen.findByRole('img', { name: 'Immich store link' })).toBeInTheDocument();
  });

  it('adds the home-screen step for web apps and lists fallbacks last', () => {
    vi.stubGlobal('navigator', { ...navigator, userAgent: 'iPhone', maxTouchPoints: 5 });
    const web = client({
      id: 'mealie-pwa',
      name: 'Mealie web app',
      kind: 'pwa',
      install: {},
      fallback: true,
      steps: ['Open Mealie.'],
    });
    const ghee = client({ id: 'ghee', name: 'Ghee', support: 'community' });
    const { container } = render(<DevicesSection service={immich([web, ghee])} address="https://host.ts.net:8450" />);
    const text = container.textContent || '';
    expect(text.indexOf('Ghee')).toBeLessThan(text.indexOf('Mealie web app'));
    expect(screen.getByText('In Safari, tap Share, then Add to Home Screen.')).toBeInTheDocument();
    expect(screen.getByText('Community')).toBeInTheDocument();
  });
});
