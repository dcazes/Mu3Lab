import { DatabaseBackup, FolderSearch, KeyRound, ShieldAlert, ShieldCheck } from 'lucide-react';
import { ExternalButton, LinkButton } from '../../components/Button';
import { Callout, Card, Collapsible, Facts, PageHeader } from '../../components/Layout';
import { Badge, Dot } from '../../components/Status';
import { relativeTime } from '../../lib/format';
import { BACKUP_APP_ID, backupsOn, launchTarget } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { BackupScheduleCard } from './BackupScheduleCard';
import { PlatformBackupCard } from './PlatformBackupCard';

export function SecuritySettings() {
  const { data } = useDashboard();
  const { identity, audit } = data;
  const backup = data.system.backup;
  const verified = backup.state === 'verified';
  const on = backupsOn(data.services.services);
  const browser = data.services.services.find((service) => service.id === BACKUP_APP_ID);
  const browserUrl = (browser && launchTarget(browser)?.url) || '';
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
      {on ? (
        <>
          <Card title="Backups">
            {verified ? (
              <Callout tone="success" icon={ShieldCheck} title="Local backup verified">
                A snapshot and integrity check succeeded on this machine. This doesn’t protect against losing the
                machine itself.
              </Callout>
            ) : (
              <Callout tone="warning" icon={ShieldAlert} title="Backup protection is not verified">
                Mu3Lab won’t report your data as protected until a snapshot and integrity check succeed. Scheduled
                backups below save everything on their own; you can also back up an app from its Advanced tab, and
                Mu3Lab saves one before every app update.
              </Callout>
            )}
            {identity.is_admin && browserUrl && (
              <div className="button-row">
                <ExternalButton size="sm" icon={FolderSearch} href={browserUrl}>
                  Open backups
                </ExternalButton>
                <span className="muted">
                  Look inside any backup and restore single files into /srv/mu3lab/backups/restores. To roll back a
                  whole app, use that app’s Backups card.
                </span>
              </div>
            )}
            <Facts
              items={[
                { label: 'Repository', value: backup.repository_present ? 'Initialized' : 'Not initialized' },
                { label: 'Integrity check', value: backup.integrity_verified ? 'Passed' : 'Not verified' },
                { label: 'Off-site copy', value: 'Not configured' },
              ]}
            />
          </Card>
          {identity.is_admin && <BackupScheduleCard />}
          {identity.is_admin && <PlatformBackupCard />}
        </>
      ) : (
        // Without Kopia backups nothing runs on a schedule; the schedule, Mu3Lab and recovery cards wait for it.
        <Callout
          tone="warning"
          icon={DatabaseBackup}
          title="Backups are off"
          action={
            identity.is_admin && (
              <LinkButton size="sm" to={`/apps/${BACKUP_APP_ID}`}>
                Install Kopia backups
              </LinkButton>
            )
          }
        >
          Kopia backups keeps encrypted copies of Mu3Lab and every app on a schedule you choose. Until it is installed,
          only app updates save a backup first.
        </Callout>
      )}
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
