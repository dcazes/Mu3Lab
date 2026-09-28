import type { Service } from '../api';

export type Stage = Service['stage'];
export type Tone = 'green' | 'amber' | 'red' | 'gray' | 'blue';

export const stageLabel: Record<Stage, string> = {
  optional: 'Productivity',
  core: 'AI',
  foundation: 'Infrastructure',
  blocked: 'Coming later',
};

export const displayStage = (service: Service): Stage =>
  ['firecrawl', 'lobehub'].includes(service.id) ? 'core' : service.stage;

export const stateLabel: Record<Service['state'], string> = {
  planned: 'Not installed',
  not_installed: 'Not installed',
  config_required: 'Needs configuration',
  queued: 'Queued',
  installing: 'Installing',
  installed: 'Installed',
  needs_setup: 'Needs setup',
  configured: 'Configured',
  starting: 'Starting',
  verifying: 'Verifying',
  running: 'Running',
  ready: 'Running',
  stopped: 'Stopped',
  updating: 'Updating',
  degraded: 'Degraded',
  failed: 'Failed',
  needs_attention: 'Needs attention',
  blocked: 'Unavailable',
};

const WORKING = new Set(['queued', 'installing', 'starting', 'verifying', 'updating']);
const PROBLEM = new Set(['failed', 'needs_attention', 'degraded']);
const SETUP = new Set(['config_required', 'needs_setup']);

export function stateTone(state: string): Tone {
  if (state === 'ready' || state === 'running') return 'green';
  if (PROBLEM.has(state)) return 'red';
  if (SETUP.has(state)) return 'amber';
  if (WORKING.has(state)) return 'blue';
  return 'gray';
}

export function categoryLabel(category: string) {
  if (!category) return '';
  if (category.toLowerCase() === 'ai') return 'AI';
  return category.charAt(0).toUpperCase() + category.slice(1);
}

export const isRunning = (service: Service) => ['ready', 'running'].includes(service.state);
export const isWorking = (service: Service) => WORKING.has(service.state);

export function isInstalled(service: Service) {
  if (service.stage === 'blocked') return false;
  if (service.installation_state) return service.installation_state !== 'not_installed';
  return !['planned', 'not_installed', 'blocked'].includes(service.state);
}

export function needsAttention(service: Service) {
  return isInstalled(service) && (PROBLEM.has(service.state) || SETUP.has(service.state));
}

export function canInstall(service: Service) {
  return (
    service.stage === 'optional' &&
    service.availability === 'available' &&
    ['planned', 'not_installed', 'degraded', 'needs_attention', 'needs_setup'].includes(service.state) &&
    service.installation_state !== 'installed'
  );
}

/** Apps people open day to day, as opposed to the AI and infrastructure plumbing. */
export function isEverydayApp(service: Service) {
  return displayStage(service) === 'optional' || service.id === 'vaultwarden';
}

// Entering through the app's own SSO button skips its local login form.
const SSO_ENTRY_PATHS: Record<string, string> = {
  mealie: '/api/auth/oauth',
  immich: '/auth/login?autoLaunch=1',
  'paperless-ngx': '/accounts/oidc/authentik/login/',
  nextcloud: '/index.php/apps/user_oidc/login/1',
  adventurelog: '/accounts/oidc/mu3lab-adventurelog/login/',
};

function ssoEntry(service: Service, url: string) {
  try {
    const parsed = new URL(url);
    const path = SSO_ENTRY_PATHS[service.id];
    if (path) return new URL(path, parsed.origin).toString();
    if (service.id !== 'litellm') return url;
    // LiteLLM's login can keep a stale HTTP origin; start at its HTTPS UI.
    parsed.protocol = 'https:';
    parsed.pathname = '/ui/';
    parsed.search = '';
    parsed.hash = '';
    return parsed.toString();
  } catch {
    return url;
  }
}

export interface LaunchTarget {
  url: string;
  label: string;
}

/** Where "Open" should take the user, or null when the app has nothing to open right now. */
export function launchTarget(service: Service): LaunchTarget | null {
  if (service.state === 'stopped' || !isInstalled(service)) return null;
  const identity = service.identity;
  const ui = service.ui;
  // Older control planes lack the identity projection; a verified UI route is still safe.
  const routeUrl = ui?.state === 'ready' && ui.url ? ui.url : '';
  const baseUrl = identity?.launch_url || routeUrl;
  if (!baseUrl) return null;
  if (!identity) return { url: ssoEntry(service, baseUrl), label: 'Open' };
  if (identity.mode === 'none') {
    if (!routeUrl) return null;
    return { url: ssoEntry(service, baseUrl), label: service.id === 'firecrawl' ? 'Open API' : 'Open' };
  }
  if (identity.mode === 'local') return routeUrl ? { url: routeUrl, label: 'Log in' } : null;
  return { url: ssoEntry(service, baseUrl), label: 'Open' };
}

export const signInLabel: Record<string, string> = {
  native_oidc: 'Single sign-on',
  trusted_header: 'Single sign-on',
  proxy_gate: 'Protected, separate login',
  local: 'Separate login',
  none: 'No sign-in',
};

export function signInSummary(service: Service) {
  const identity = service.identity;
  if (!identity) return { label: humanizeAuth(service.auth), tone: 'gray' as Tone, repairable: false };
  const tone: Tone =
    identity.state === 'ready'
      ? 'green'
      : identity.state === 'degraded'
        ? 'red'
        : identity.state === 'unsupported'
          ? 'gray'
          : 'amber';
  return {
    label: signInLabel[identity.mode] || identity.mode,
    tone,
    repairable: !['ready', 'unsupported'].includes(identity.state) && !['none', 'local'].includes(identity.mode),
  };
}

function humanizeAuth(auth: Service['auth']) {
  return { oidc: 'Single sign-on', proxy: 'Protected', trusted_header: 'Single sign-on', local: 'Separate login' }[
    auth as string
  ] as string;
}
