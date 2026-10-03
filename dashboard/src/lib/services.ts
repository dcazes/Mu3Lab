import type { Service } from '../api';

export type Stage = Service['stage'];
export type Group = Service['group'];
export type Tone = 'green' | 'amber' | 'red' | 'gray' | 'blue';

export const groupLabel: Record<Group, string> = {
  apps: 'Personal apps',
  ai: 'AI',
  infrastructure: 'Infrastructure',
};

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
  uninstalling: 'Uninstalling',
  running: 'Running',
  ready: 'Running',
  stopped: 'Stopped',
  updating: 'Updating',
  degraded: 'Degraded',
  failed: 'Failed',
  needs_attention: 'Needs attention',
};

const WORKING = new Set(['queued', 'installing', 'starting', 'verifying', 'uninstalling', 'updating']);
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
  if (service.installation_state) return service.installation_state !== 'not_installed';
  return !['planned', 'not_installed'].includes(service.state);
}

export function needsAttention(service: Service) {
  return isInstalled(service) && (PROBLEM.has(service.state) || SETUP.has(service.state));
}

export function canInstall(service: Service) {
  return (
    service.stage === 'optional' &&
    ['planned', 'not_installed', 'degraded', 'needs_attention', 'needs_setup'].includes(service.state) &&
    service.installation_state !== 'installed'
  );
}

/** Apps people open day to day, as opposed to the AI and infrastructure plumbing. */
export function isEverydayApp(service: Service) {
  return service.group === 'apps';
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
  const routeUrl = ui?.state === 'ready' && ui.url ? ui.url : '';
  // The control plane builds the full address, sign-in entry path included.
  const url = identity?.launch_url || routeUrl;
  if (!url) return null;
  if (identity?.mode === 'local') return routeUrl ? { url: routeUrl, label: 'Log in' } : null;
  if (identity?.mode === 'none' && !routeUrl) return null;
  return { url, label: ui?.launch_label || 'Open' };
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
  if (!identity) return { label: humanizeAuth(service.auth), tone: 'gray' as Tone };
  const tone: Tone =
    identity.state === 'ready'
      ? 'green'
      : identity.state === 'degraded'
        ? 'red'
        : identity.state === 'unsupported'
          ? 'gray'
          : 'amber';
  return { label: signInLabel[identity.mode] || identity.mode, tone };
}

function humanizeAuth(auth: Service['auth']) {
  return { oidc: 'Single sign-on', proxy: 'Protected', trusted_header: 'Single sign-on', local: 'Separate login' }[
    auth as string
  ] as string;
}
