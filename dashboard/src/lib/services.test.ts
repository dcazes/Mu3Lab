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

  it.each(['actual-budget', 'lobehub', 'paperless-ngx'])('starts %s through its session-aware launcher', (id) => {
    const app = service(id, id, 'optional', {
      identity: identity({ launch_url: 'https://host.ts.net:8451/login', state: 'migration_required' }),
    });
    expect(launchTarget(app)).toEqual({ url: 'https://host.ts.net:8451/__mu3lab/login', label: 'Open' });
  });

  it('enters SSO apps through their Authentik login path', () => {
    const nextcloud = service('nextcloud', 'Nextcloud', 'optional', {
      identity: identity({ launch_url: 'https://host.ts.net:8453' }),
    });
    expect(launchTarget(nextcloud)).toEqual({
      url: 'https://host.ts.net:8453/index.php/apps/user_oidc/login/1',
      label: 'Open',
    });
  });

  it('keeps apps launchable while owner migration is pending', () => {
    const nextcloud = service('nextcloud', 'Nextcloud', 'optional', {
      identity: identity({ state: 'migration_required' }),
    });
    expect(launchTarget(nextcloud)?.label).toBe('Open');
  });

  it('never offers to open a stopped app', () => {
    const app = service('nextcloud', 'Nextcloud', 'optional', { state: 'stopped', identity: identity() });
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

  it('opens a verified legacy route when the identity projection is missing', () => {
    const app = service('mealie', 'Mealie', 'optional', { ui: readyUi('https://host.ts.net:8450') });
    expect(launchTarget(app)?.url).toBe('https://host.ts.net:8450/api/auth/oauth');
  });

  it('starts LiteLLM at its HTTPS UI instead of a stale login redirect', () => {
    const litellm = service('litellm', 'LiteLLM', 'core', {
      identity: identity({ mode: 'proxy_gate', launch_url: 'http://host.ts.net:8445/sso/key/generate?x=1' }),
    });
    expect(launchTarget(litellm)?.url).toBe('https://host.ts.net:8445/ui/');
  });

  it('labels Firecrawl as an API', () => {
    const firecrawl = service('firecrawl', 'Firecrawl', 'optional', {
      identity: identity({ mode: 'none', launch_url: 'https://host.ts.net:8460' }),
      ui: readyUi('https://host.ts.net:8460'),
    });
    expect(launchTarget(firecrawl)?.label).toBe('Open API');
  });
});

describe('service state helpers', () => {
  it('flags installed apps with problems but not uninstalled ones', () => {
    expect(needsAttention(service('a', 'A', 'optional', { state: 'failed' }))).toBe(true);
    expect(
      needsAttention(service('b', 'B', 'optional', { state: 'failed', installation_state: 'not_installed' })),
    ).toBe(false);
  });

  it('only offers installation for available optional apps', () => {
    expect(
      canInstall(service('a', 'A', 'optional', { state: 'not_installed', installation_state: 'not_installed' })),
    ).toBe(true);
    expect(canInstall(service('b', 'B', 'blocked', { state: 'blocked', installation_state: 'not_installed' }))).toBe(
      false,
    );
  });

  it('offers sign-in repair only for SSO apps that are not ready', () => {
    expect(
      signInSummary(service('a', 'A', 'optional', { identity: identity({ state: 'unconfigured' }) })).repairable,
    ).toBe(true);
    expect(
      signInSummary(service('b', 'B', 'optional', { identity: identity({ mode: 'local', state: 'degraded' }) }))
        .repairable,
    ).toBe(false);
  });
});
