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

/** What a working app is doing, from the job that made it busy. */
const WORKING_LABEL: Record<string, string> = {
  install: 'Installing',
  retry_setup: 'Setting up',
  start: 'Starting',
  stop: 'Stopping',
  restart: 'Restarting',
  update: 'Updating',
  restore: 'Restoring',
  uninstall: 'Uninstalling',
  uninstall_delete_data: 'Uninstalling',
};

/** The status word for one app: "Starting…" rather than "Working" while its job runs. */
export function statusText(service: Service): string {
  if (service.backing_up) return 'Backing up…';
  if (service.display_state === 'working') {
    const verb = WORKING_LABEL[service.last_job?.action || ''];
    if (verb && ['queued', 'running'].includes(service.last_job?.state || '')) return `${verb}…`;
  }
  return stateLabel[service.display_state] || service.display_state;
}

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

/** Apps that only work together; each app's manifest names its stack (``stack:``). */
export const STACKS: { id: string; label: string }[] = [
  { id: 'movies-and-shows', label: 'Movies and shows' },
  { id: 'chat', label: 'Chat' },
  { id: 'voice', label: 'Voice' },
];

export interface LauncherSection {
  id: string;
  label: string;
  apps: Service[];
  /** A stack: its apps work only together, so a stopped one leaves it incomplete. */
  stack: boolean;
  /** AI and system apps most people never open: folded until someone opens them. */
  foldedByDefault: boolean;
}

/** A stack's apps that people open come first, the helpers others need after (qBittorrent, LiteLLM). */
function frontFirst(apps: Service[]): Service[] {
  const needs = (service: Service, seen = new Set<string>()): string[] =>
    service.dependencies.flatMap((id) => {
      if (seen.has(id)) return [];
      seen.add(id);
      const found = apps.find((app) => app.id === id);
      return [id, ...(found ? needs(found, seen) : [])];
    });
  const neededBy = new Map(apps.map((app) => [app.id, 0]));
  for (const app of apps) for (const id of new Set(needs(app))) neededBy.set(id, (neededBy.get(id) ?? 0) + 1);
  return [...apps].sort(
    (a, b) => (neededBy.get(a.id) ?? 0) - (neededBy.get(b.id) ?? 0) || a.name.localeCompare(b.name),
  );
}

/**
 * Installed apps as the launcher shows them: everyday apps by category, then each stack, then
 * the other AI apps and the system apps. An unknown stack still shows, after the known ones.
 */
export function launcherSections(services: Service[]): LauncherSection[] {
  const loose = services.filter((service) => !service.stack);
  const stackIds = [
    ...STACKS.map((item) => item.id),
    ...new Set(services.map((service) => service.stack || '').filter((id) => id && !STACKS.some((s) => s.id === id))),
  ];
  const stacks = stackIds
    .map((id) => ({
      id: `stack-${id}`,
      label: STACKS.find((item) => item.id === id)?.label || categoryLabel(id.replaceAll('-', ' ')),
      apps: frontFirst(services.filter((service) => service.stack === id)),
      stack: true,
      foldedByDefault: false,
    }))
    .filter((section) => section.apps.length > 0);
  const rest = [
    { id: 'ai', label: 'AI', apps: loose.filter((service) => service.group === 'ai') },
    { id: 'system', label: 'System', apps: loose.filter((service) => service.group === 'infrastructure') },
  ]
    .filter((section) => section.apps.length > 0)
    .map((section) => ({ ...section, stack: false, foldedByDefault: true }));
  return [
    ...appSections(loose.filter(isEverydayApp)).map((section) => ({
      ...section,
      stack: false,
      foldedByDefault: false,
    })),
    ...stacks,
    ...rest,
  ];
}

const listNames = (apps: Service[]) =>
  apps.length > 2
    ? `${apps[0].name}, ${apps[1].name} and ${apps.length - 2} more`
    : apps.map((s) => s.name).join(' and ');

/** "6 of 7 running" and what is off, for a launcher section's header. */
export function sectionSummary(section: Pick<LauncherSection, 'apps' | 'stack'>): {
  count: string;
  detail: string;
  tone: Tone;
} {
  const { apps, stack } = section;
  const running = apps.filter(isRunning).length;
  const broken = apps.filter(needsAttention);
  const busy = apps.filter((service) => isWorking(service) || service.display_state === 'checking');
  const off = apps.filter((service) => service.display_state === 'stopped' || service.display_state === 'needs_setup');
  // One app: its own state says it all ("Stopped"), with no count.
  if (apps.length === 1) {
    const [only] = apps;
    return { count: statusText(only), detail: '', tone: isRunning(only) ? 'green' : stateTone(only.display_state) };
  }
  const count = `${running} of ${apps.length} running`;
  if (broken.length)
    return { count, detail: `${listNames(broken)} ${broken.length > 1 ? 'need' : 'needs'} attention`, tone: 'red' };
  if (busy.length)
    return { count, detail: busy.length === 1 ? statusText(busy[0]) : `${busy.length} busy`, tone: 'blue' };
  if (off.length) return { count, detail: `${listNames(off)} stopped`, tone: stack ? 'amber' : 'gray' };
  return { count, detail: '', tone: 'green' };
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
