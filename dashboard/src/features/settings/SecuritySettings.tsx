import { KeyRound, ShieldAlert, ShieldCheck } from 'lucide-react';
import { Callout, Card, Collapsible, Facts, PageHeader } from '../../components/Layout';
import { Badge, Dot } from '../../components/Status';
import { relativeTime } from '../../lib/format';
import { useDashboard } from '../../state/dashboard';

export function SecuritySettings() {
  const { data } = useDashboard();
  const { identity, audit } = data;
  const backup = data.system.backup;
  const verified = backup.state === 'verified';
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
      <Card title="Backups">
        {verified ? (
          <Callout tone="success" icon={ShieldCheck} title="Local backup verified">
            A snapshot and integrity check succeeded on this machine. This doesn’t protect against losing the machine
            itself.
          </Callout>
        ) : (
          <Callout tone="warning" icon={ShieldAlert} title="Backup protection is not verified">
            Mu3Lab won’t report your data as protected until a snapshot and integrity check succeed. Back up an app from
            its Advanced tab; Mu3Lab also saves one before every app update. Scheduled backups are coming in a future
            update.
          </Callout>
        )}
        <Facts
          items={[
            { label: 'Repository', value: backup.repository_present ? 'Initialized' : 'Not initialized' },
            { label: 'Integrity check', value: backup.integrity_verified ? 'Passed' : 'Not verified' },
            { label: 'Off-site copy', value: 'Not configured' },
          ]}
        />
      </Card>
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
