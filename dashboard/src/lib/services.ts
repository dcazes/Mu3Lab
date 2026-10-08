import type { Service } from '../api';

export type Stage = Service['stage'];
export type Group = Service['group'];
export type Tone = 'green' | 'amber' | 'red' | 'gray' | 'blue';

export const groupLabel: Record<Group, string> = {
  apps: 'Personal apps',
  ai: 'AI',
  infrastructure: 'Infrastructure',
};

export const stateLabel: Record<Service['display_state'], string> = {
  running: 'Running',
  stopped: 'Stopped',
  working: 'Working',
  checking: 'Checking',
  not_installed: 'Not installed',
  needs_attention: 'Needs attention',
};

export function stateTone(state: string): Tone {
  return (
    (
      {
        running: 'green',
        stopped: 'gray',
        working: 'blue',
        checking: 'blue',
        not_installed: 'gray',
        needs_attention: 'red',
      } as Record<string, Tone>
    )[state] || 'gray'
  );
}

export function categoryLabel(category: string) {
  if (!category) return '';
  if (category.toLowerCase() === 'ai') return 'AI';
  return category.charAt(0).toUpperCase() + category.slice(1);
}

export const isRunning = (service: Service) => service.display_state === 'running';
export const isWorking = (service: Service) => service.display_state === 'working';
export const isInstalled = (service: Service) => service.installed;
export const needsAttention = (service: Service) => service.display_state === 'needs_attention' && service.installed;
export const canInstall = (service: Service) =>
  service.stage === 'optional' &&
  !service.installed &&
  (service.allowed_actions || []).some((action) => action === 'install' || action === 'retry_setup');

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
  if (service.display_state === 'stopped' || !isInstalled(service)) return null;
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

export type CheckName = keyof NonNullable<Service['checks']>;

export const checkLabel: Record<CheckName, string> = {
  process: 'Health check',
  route: 'Private address',
  sign_in: 'Sign-in',
};

const checkTone: Record<string, Tone> = {
  pass: 'green',
  checking: 'blue',
  pending: 'blue',
  fail: 'red',
  stale: 'amber',
  not_required: 'gray',
};

/** The checks this app requires, in the order they gate "usable". */
export function requiredChecks(service: Service) {
  const checks = service.checks;
  if (!checks) return [];
  return (Object.keys(checkLabel) as CheckName[])
    .filter((name) => checks[name].state !== 'not_required')
    .map((name) => ({ name, label: checkLabel[name], tone: checkTone[checks[name].state] || 'gray', ...checks[name] }));
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
