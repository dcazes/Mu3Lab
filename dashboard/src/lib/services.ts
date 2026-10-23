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
  needs_setup: 'Needs setup',
  needs_attention: 'Needs attention',
};

/** Red is kept for failures, so it stands out; a stopped app is a choice, shown in neutral grey. */
export function stateTone(state: string): Tone {
  return (
    (
      {
        running: 'green',
        stopped: 'gray',
        working: 'blue',
        checking: 'blue',
        not_installed: 'gray',
        needs_setup: 'amber',
        needs_attention: 'red',
      } as Record<string, Tone>
    )[state] || 'gray'
  );
}

/** Discover's sections, in order; each app's manifest names one. */
export const CATEGORIES: { id: string; label: string }[] = [
  { id: 'finance', label: 'Finance' },
  { id: 'home', label: 'Home & family' },
  { id: 'media', label: 'Photos & media' },
  { id: 'documents', label: 'Documents & notes' },
  { id: 'places', label: 'Places & travel' },
  { id: 'knowledge', label: 'Knowledge & search' },
  { id: 'ai', label: 'AI & voice' },
  { id: 'system', label: 'Backups & system' },
];

/** Apps grouped into Discover's sections, in order; an unknown category still shows, last. */
export function appSections(services: Service[]) {
  return [
    ...CATEGORIES,
    ...[...new Set(services.map((service) => service.category))]
      .filter((id) => !CATEGORIES.some((item) => item.id === id))
      .map((id) => ({ id, label: categoryLabel(id) })),
  ]
    .map((category) => ({ ...category, apps: services.filter((service) => service.category === category.id) }))
    .filter((category) => category.apps.length > 0);
}

export function categoryLabel(category: string) {
  if (!category) return '';
  const known = CATEGORIES.find((item) => item.id === category);
  if (known) return known.label;
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
  if (service.display_state === 'stopped' || service.display_state === 'needs_setup' || !isInstalled(service))
    return null;
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
        : identity.state === 'unsupported' || identity.state === 'off'
          ? 'gray'
          : 'amber';
  return { label: signInLabel[identity.mode] || identity.mode, tone };
}

function humanizeAuth(auth: Service['auth']) {
  return { oidc: 'Single sign-on', proxy: 'Protected', trusted_header: 'Single sign-on', local: 'Separate login' }[
    auth as string
  ] as string;
}

/** Kopia backups, the optional app that makes scheduled and on-demand backups. */
export const BACKUP_APP_ID = 'kopia-ui';

/** Whether backups run: Kopia backups is installed. Pre-update backups work either way. */
export function backupsOn(services: Service[]): boolean {
  const app = services.find((service) => service.id === BACKUP_APP_ID);
  return Boolean(app && isInstalled(app));
}
