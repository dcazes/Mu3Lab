import { Download, Eye, KeyRound, ShieldAlert, ShieldCheck, Vault } from 'lucide-react';
import { useState } from 'react';
import { type CredentialHandoff, type CredentialReveal, postApi } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card, Collapsible, Facts, PageHeader } from '../../components/Layout';
import { Badge, Dot } from '../../components/Status';
import { relativeTime } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';
import { VaultSetupDialog } from './VaultSetup';

function RecoveryCredentials() {
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const handoffs = useApi<{ handoffs: CredentialHandoff[] }>('/api/v1/credential-handoffs');
  const [revealed, setRevealed] = useState<Record<string, CredentialReveal>>({});
  const [vaultOpen, setVaultOpen] = useState(false);
  const items = handoffs.data?.handoffs || [];
  if (!items.length) return null;

  const reveal = (id: string) =>
    run(`reveal-${id}`, async () => {
      const result = await postApi<{ credential: CredentialReveal }>(`/api/v1/credential-handoffs/${id}/reveal`);
      setRevealed((current) => ({ ...current, [id]: result.credential }));
    });
  const saved = async (id: string) => {
    if (
      !(await confirm({
        title: 'Is this password saved?',
        description: 'Mu3Lab deletes its temporary encrypted copy. It can’t be shown again.',
        confirmLabel: 'Yes, it’s saved',
      }))
    )
      return;
    await run(`confirm-${id}`, () => postApi(`/api/v1/credential-handoffs/${id}/confirm`), 'Temporary copy deleted');
    void handoffs.reload();
  };
  const exportAll = async () => {
    if (
      !(await confirm({
        title: 'Export as an unencrypted file?',
        description:
          'Anyone with the file can read these passwords. Import it into Vaultwarden (Bitwarden JSON format), then delete it.',
        confirmLabel: 'Export',
        tone: 'danger',
      }))
    )
      return;
    await run(
      'export',
      async () => {
        const credentials = await Promise.all(
          items.map((item) =>
            postApi<{ credential: CredentialReveal }>(`/api/v1/credential-handoffs/${item.id}/reveal`).then(
              (result) => result.credential,
            ),
          ),
        );
        const payload = {
          encrypted: false,
          folders: [],
          items: credentials.map((credential) => ({
            type: 1,
            name: `Mu3Lab ${credential.service_id} recovery`,
            notes: 'Generated local recovery account. Normal access may use Authentik SSO.',
            favorite: false,
            login: {
              uris: credential.login_url ? [{ match: null, uri: credential.login_url }] : [],
              username: credential.email || credential.username,
              password: credential.password,
              totp: null,
            },
            collectionIds: [],
          })),
        };
        const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
        const link = document.createElement('a');
        link.href = url;
        link.download = `mu3lab-recovery-credentials-${new Date().toISOString().slice(0, 10)}.json`;
        link.click();
        URL.revokeObjectURL(url);
      },
      `${items.length} credential${items.length === 1 ? '' : 's'} exported. Delete the file after importing.`,
    );
  };

  return (
    <Card
      title="New app passwords"
      description="Authentik sign-in needs no password, but Mu3Lab created these backup admin logins while installing apps. Save them in Vaultwarden; they expire after 24 hours."
      actions={
        <>
          <Button icon={Download} loading={pending === 'export'} onClick={() => void exportAll()}>
            Export credentials
          </Button>
          <Button variant="primary" icon={Vault} onClick={() => setVaultOpen(true)}>
            Save to Vaultwarden
          </Button>
        </>
      }
      flush
    >
      {vaultOpen && (
        <VaultSetupDialog open onClose={() => setVaultOpen(false)} onSaved={() => void handoffs.reload()} />
      )}
      <div className="rows">
        {items.map((item) => {
          const secret = revealed[item.id];
          return (
            <div className="credential" key={item.id}>
              <div className="row">
                <span className="row-text">
                  <b>{item.service_id}</b>
                  <small>Expires {relativeTime(item.expires_at)}</small>
                </span>
                {!secret && (
                  <Button
                    size="sm"
                    icon={Eye}
                    loading={pending === `reveal-${item.id}`}
                    onClick={() => void reveal(item.id)}
                  >
                    Show
                  </Button>
                )}
              </div>
              {secret && (
                <div className="credential-body">
                  <CopyField value={secret.email || secret.username} label="Username" />
                  <CopyField value={secret.password} label="Password" />
                  <div className="button-row">
                    {secret.login_url && <ExternalButton href={secret.login_url}>Open app</ExternalButton>}
                    <Button variant="primary" onClick={() => void saved(item.id)}>
                      I saved it
                    </Button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

export function SecuritySettings() {
  const { data } = useDashboard();
  const { identity, audit } = data;
  const backup = data.system.backup;
  const verified = backup.state === 'verified';
  return (
    <>
      <PageHeader title="Security & backups" description="Who can manage Mu3Lab and how your data is protected." />
      <RecoveryCredentials />
      <Card title="Your access">
        <Facts
          items={[
            { label: 'Signed in as', value: identity.display_name || identity.username || '—' },
            { label: 'Email', value: identity.email || '—' },
            {
              label: 'Role',
              value: identity.writes_enabled ? <Badge tone="green">Administrator</Badge> : <Badge>Read only</Badge>,
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
          <Callout tone="warning" icon={ShieldAlert} title="Backups aren’t set up yet">
            Mu3Lab won’t report your data as protected until a snapshot and integrity check succeed. Automatic backups
            and restore are coming in a future update.
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
