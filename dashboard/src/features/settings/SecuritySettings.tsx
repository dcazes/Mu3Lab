import { FolderSearch, KeyRound, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { BackupProtection, PlatformBackupStatus } from '../../api';
import { ExternalButton, LinkButton } from '../../components/Button';
import { Callout, Card, Collapsible, Facts, PageHeader } from '../../components/Layout';
import { Badge, Dot } from '../../components/Status';
import { relativeTime } from '../../lib/format';
import { BACKUP_APP_ID, backupsOn, launchTarget } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { useApi } from '../../lib/useApi';
import { BackupScheduleSection } from './BackupSchedule';
import { RecoverySheetSection } from './RecoverySheet';

function protection(value: BackupProtection | null | undefined) {
  if (!value) return <Badge>Not backed up yet</Badge>;
  const when = value.completed_at ? ` · ${relativeTime(value.completed_at)}` : '';
  if (value.state === 'checked') return <Badge tone="green">{`Checked${when}`}</Badge>;
  if (value.state === 'missing') return <Badge tone="amber">No backup yet</Badge>;
  return <Badge tone="amber">{`${value.state === 'stale' ? 'Out of date' : 'Not checked yet'}${when}`}</Badge>;
}

/**
 * Backups in one card: whether they're verified, the schedule, Mu3Lab's own core, and the recovery sheet.
 * Without Kopia backups it folds to one line with the way to install it.
 */
function BackupsCard() {
  const { data } = useDashboard();
  const { identity } = data;
  const admin = identity.is_admin;
  const backup = data.system.backup;
  const on = backupsOn(data.services.services);
  // Mu3Lab's own settings, secrets and core apps are a source in every scheduled backup.
  const core = useApi<PlatformBackupStatus>(on && admin ? '/api/v1/system/backups/platform' : null);
  const browser = data.services.services.find((service) => service.id === BACKUP_APP_ID);
  const browserUrl = (browser && launchTarget(browser)?.url) || '';
  const install = admin && (
    <LinkButton size="sm" to={`/apps/${BACKUP_APP_ID}`}>
      Install Kopia backups
    </LinkButton>
  );
  if (!on)
    return (
      <Card
        title="Backups"
        description="Backups are off: Kopia backups isn’t installed."
        actions={install}
        collapsible
        defaultOpen={false}
      >
        <p className="muted">
          Kopia backups keeps encrypted copies of Mu3Lab and every app on a schedule you choose. Until it is installed,
          only app updates save a backup first.
        </p>
        {admin && <BackupScheduleSection disabled />}
      </Card>
    );
  return (
    <Card title="Backups">
      {backup.state === 'verified' ? (
        <Callout tone="success" icon={ShieldCheck} title="Local backup verified">
          A snapshot and integrity check succeeded on this machine. This doesn’t protect against losing the machine
          itself.
        </Callout>
      ) : (
        <Callout tone="warning" icon={ShieldAlert} title="Backup protection is not verified">
          Mu3Lab won’t report your data as protected until a snapshot and integrity check succeed. Scheduled backups
          save everything on their own; you can also back up an app from its Advanced tab, and Mu3Lab saves one before
          every app update.
        </Callout>
      )}
      {admin && <BackupScheduleSection />}
      <Facts
        items={[
          ...(admin ? [{ label: 'Mu3Lab core', value: core.data ? protection(core.data.platform) : '—' }] : []),
          { label: 'Repository', value: backup.repository_present ? 'Initialized' : 'Not initialized' },
          { label: 'Integrity check', value: backup.integrity_verified ? 'Passed' : 'Not verified' },
          { label: 'Off-site copy', value: 'Not configured' },
        ]}
      />
      {admin && browserUrl && (
        <div className="button-row">
          <ExternalButton size="sm" icon={FolderSearch} href={browserUrl}>
            Open backups
          </ExternalButton>
          <span className="muted">
            Look inside any backup and restore single files into /srv/mu3lab/backups/restores. To roll back a whole app,
            use that app’s Backups card.
          </span>
        </div>
      )}
      {admin && <RecoverySheetSection status={core.data?.recovery_sheet} onOpened={() => void core.reload()} />}
    </Card>
  );
}

export function SecuritySettings() {
  const { data } = useDashboard();
  const { identity, audit } = data;
  return (
    <>
      <PageHeader title="Security & backups" description="Who can manage Mu3Lab and how your data is protected." />
      <Card title="Your access">
        <Facts
          items={[
            { label: 'Signed in as', value: identity.display_name || identity.username || '—' },
            { label: 'Email', value: identity.email || '—' },
            {
              label: 'Role',
              value: identity.is_admin ? (
                <Badge tone="green">Administrator</Badge>
              ) : identity.writes_enabled ? (
                <Badge>Household member</Badge>
              ) : (
                <Badge>Read only</Badge>
              ),
            },
            {
              label: 'Protection',
              value: (
                <span className="inline">
                  <Dot tone={identity.writes_enabled ? 'green' : 'amber'} />
                  {identity.writes_enabled ? 'Tailscale + Authentik verified' : identity.detail}
                </span>
              ),
            },
          ]}
        />
      </Card>
      <BackupsCard />
      <Collapsible title={`Audit log${audit.available ? ` (${audit.events.length})` : ''}`}>
        {audit.events.length ? (
          <div className="rows compact">
            {audit.events.slice(0, 50).map((event) => (
              <div className="row" key={event.id}>
                <span className="row-text">
                  <b>{event.event}</b>
                  <small>
                    {event.actor} · {event.detail}
                  </small>
                </span>
                <small className="row-time">{relativeTime(event.created_at)}</small>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">
            <KeyRound className="inline-icon" /> No audit events recorded yet.
          </p>
        )}
      </Collapsible>
    </>
  );
}
