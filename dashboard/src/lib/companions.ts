export interface CompanionLink {
  platform: string;
  url: string;
}

export interface Companion {
  /** What to enter in the companion app's server field. */
  serverHint: string;
  apps: CompanionLink[];
  note?: string;
}

// Official clients only. Apps without one are offered as installable web apps instead.
const companions: Record<string, Companion> = {
  immich: {
    serverHint: 'Open the Immich app, enter this server URL, then choose "Login with Authentik".',
    apps: [
      { platform: 'iPhone & iPad', url: 'https://apps.apple.com/app/immich/id1613945652' },
      { platform: 'Android', url: 'https://play.google.com/store/apps/details?id=app.alextran.immich' },
      { platform: 'F-Droid', url: 'https://f-droid.org/packages/app.alextran.immich/' },
    ],
  },
  nextcloud: {
    serverHint: 'Enter this server address in the Nextcloud app and approve the login in your browser.',
    apps: [
      { platform: 'iPhone & iPad', url: 'https://apps.apple.com/app/nextcloud/id1125420102' },
      { platform: 'Android', url: 'https://play.google.com/store/apps/details?id=com.nextcloud.client' },
      { platform: 'Desktop sync', url: 'https://nextcloud.com/install/' },
    ],
    note: 'Calendars and contacts can also sync to your phone using CalDAV and CardDAV from the same server.',
  },
  vaultwarden: {
    serverHint:
      'Vaultwarden works with the official Bitwarden apps. Choose "Self-hosted" when signing in and enter this server URL.',
    apps: [{ platform: 'All platforms', url: 'https://bitwarden.com/download/' }],
  },
  'actual-budget': {
    serverHint: 'In the desktop app, choose "Use a self-hosted server" and enter this URL.',
    apps: [{ platform: 'Desktop app', url: 'https://actualbudget.org/download/' }],
  },
};

export function companionFor(serviceId: string): Companion | undefined {
  return companions[serviceId];
}
