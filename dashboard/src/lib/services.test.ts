import { describe, expect, it } from 'vitest';
import { identity, readyUi, service } from '../test/fixtures';
import { canInstall, launchTarget, needsAttention, signInSummary } from './services';

describe('launchTarget', () => {
  it('preserves the managed Nextcloud provider ID reported by the server', () => {
    const app = service('nextcloud', 'Nextcloud', 'optional', {
      identity: identity({ launch_url: 'https://host.ts.net:8453/index.php/apps/user_oidc/login/7' }),
    });
    expect(launchTarget(app)?.url).toBe('https://host.ts.net:8453/index.php/apps/user_oidc/login/7');
  });

  it('opens exactly the launch address the control plane built, sign-in path included', () => {
    const app = service('actual-budget', 'Actual Budget', 'optional', {
      identity: identity({ launch_url: 'https://host.ts.net:8448/__mu3lab/login', state: 'migration_required' }),
    });
    expect(launchTarget(app)).toEqual({ url: 'https://host.ts.net:8448/__mu3lab/login', label: 'Open' });
  });

  it('keeps apps launchable while owner migration is pending', () => {
    const nextcloud = service('nextcloud', 'Nextcloud', 'optional', {
      identity: identity({ state: 'migration_required' }),
    });
    expect(launchTarget(nextcloud)?.label).toBe('Open');
  });

  it('never offers to open a stopped app', () => {
    const app = service('nextcloud', 'Nextcloud', 'optional', { display_state: 'stopped', identity: identity() });
    expect(launchTarget(app)).toBeNull();
  });

  it('labels separate-login apps as Log in and uses their verified route', () => {
    const vaultwarden = service('vaultwarden', 'Vaultwarden', 'foundation', {
      identity: identity({ mode: 'local', launch_url: 'https://host.ts.net:8444' }),
      ui: readyUi('https://host.ts.net:8444'),
    });
    expect(launchTarget(vaultwarden)).toEqual({ url: 'https://host.ts.net:8444', label: 'Log in' });
  });

  it('omits launch for internal services without a browser route', () => {
    const caddy = service('ingress', 'Caddy', 'foundation', {
      identity: identity({ mode: 'none', state: 'unsupported', launch_url: '' }),
      ui: { state: 'unavailable', url: '', label: '', authentication: 'none', reason: 'No dashboard' },
    });
    expect(launchTarget(caddy)).toBeNull();
  });

  it('opens the verified route when the identity projection is missing', () => {
    const app = service('mealie', 'Mealie', 'optional', { ui: readyUi('https://host.ts.net:8450') });
    expect(launchTarget(app)?.url).toBe('https://host.ts.net:8450');
  });

  it('uses the button label from the app manifest', () => {
    const firecrawl = service('firecrawl', 'Firecrawl', 'core', {
      identity: identity({ mode: 'none', launch_url: 'https://host.ts.net:8460' }),
      ui: { ...readyUi('https://host.ts.net:8460')!, launch_label: 'Open API' },
    });
    expect(launchTarget(firecrawl)?.label).toBe('Open API');
  });
});

describe('service state helpers', () => {
  it('flags installed apps with problems but not uninstalled ones', () => {
    expect(needsAttention(service('a', 'A', 'optional', { display_state: 'needs_attention' }))).toBe(true);
    expect(
      needsAttention(
        service('b', 'B', 'optional', {
          display_state: 'needs_attention',
          installed: false,
          allowed_actions: ['install'],
        }),
      ),
    ).toBe(false);
  });

  it('only offers installation for available optional apps', () => {
    expect(
      canInstall(
        service('a', 'A', 'optional', {
          display_state: 'not_installed',
          installed: false,
          allowed_actions: ['install'],
        }),
      ),
    ).toBe(true);
    expect(
      canInstall(
        service('b', 'B', 'core', { display_state: 'not_installed', installed: false, allowed_actions: ['install'] }),
      ),
    ).toBe(false);
  });

  it('marks broken sign-in red and working sign-in green', () => {
    expect(signInSummary(service('a', 'A', 'optional', { identity: identity({ state: 'degraded' }) })).tone).toBe(
      'red',
    );
    expect(signInSummary(service('b', 'B', 'optional', { identity: identity({ state: 'ready' }) })).tone).toBe('green');
  });
});
