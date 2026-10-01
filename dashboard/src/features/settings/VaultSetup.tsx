import { CheckCircle2, Vault } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { ApiError, postApi, postJsonApi, type VaultSetupResult, type VaultStatus } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { Dialog } from '../../components/Dialog';
import { Callout, Card } from '../../components/Layout';
import { errorText } from '../../lib/format';
import { launchTarget } from '../../lib/services';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

const EXTENSION_URL = 'https://bitwarden.com/download/#downloads-web-browser';

function useVaultUrl() {
  const { data } = useDashboard();
  const vaultwarden = data.services.services.find((service) => service.id === 'vaultwarden');
  return (vaultwarden && launchTarget(vaultwarden)?.url) || '';
}

function Summary({ result }: { result: VaultSetupResult }) {
  const saved = result.created.length + result.updated.length;
  const lines = [
    result.created.length ? `Added: ${result.created.join(', ')}.` : '',
    result.updated.length ? `Updated passwords: ${result.updated.join(', ')}.` : '',
    result.skipped.length ? `Already saved: ${result.skipped.join(', ')}.` : '',
  ];
  return (
    <Callout tone="success" icon={CheckCircle2} title={saved ? `${saved} logins saved` : 'Your vault is up to date'}>
      {lines.filter(Boolean).join(' ')}
    </Callout>
  );
}

export function VaultSetupDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const { data } = useDashboard();
  const vaultUrl = useVaultUrl();
  const [email, setEmail] = useState(data.identity.email || '');
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [needsCode, setNeedsCode] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<VaultSetupResult | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPending(true);
    setError('');
    try {
      const response = await postJsonApi<VaultSetupResult>('/api/v1/vault/setup', {
        email,
        master_password: password,
        totp: needsCode ? totp : '',
      });
      setResult(response);
      onSaved?.();
    } catch (cause) {
      if (cause instanceof ApiError && cause.body.code === 'two_factor_required') setNeedsCode(true);
      setError(errorText(cause));
    } finally {
      // The master password is only needed for this one request.
      setPassword('');
      setTotp('');
      setPending(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Save logins to Vaultwarden"
      description="Mu3Lab signs in to your vault once to add your app logins and a sign-up entry for each AI provider. Your master password is used for this request only and is never stored."
    >
      {result ? (
        <div className="form">
          <Summary result={result} />
          <p className="hint">
            <span>
              To autofill these, install the Bitwarden browser extension, choose <b>Self-hosted</b> when signing in, and
              enter this server URL:
            </span>
          </p>
          {vaultUrl && <CopyField value={vaultUrl} label="Server URL" />}
          <footer className="form-footer">
            <ExternalButton href={EXTENSION_URL}>Get the extension</ExternalButton>
            <span className="spacer" />
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </footer>
        </div>
      ) : (
        <form className="form" onSubmit={submit}>
          <label className="field">
            <span>Vaultwarden email</span>
            <input
              required
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Master password</span>
            <input
              required
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {needsCode && (
            <label className="field">
              <span>Two-step login code</span>
              <input
                required
                inputMode="numeric"
                autoComplete="one-time-code"
                value={totp}
                onChange={(event) => setTotp(event.target.value)}
              />
            </label>
          )}
          {error && <p className="error-text">{error}</p>}
          <footer className="form-footer">
            <span className="spacer" />
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" type="submit" loading={pending}>
              Save to vault
            </Button>
          </footer>
        </form>
      )}
    </Dialog>
  );
}

const PERSON_STATE: Record<string, string> = {
  up_to_date: 'Saved. Bitwarden fills these logins in for them.',
  partly_saved: 'Some logins are not saved yet; Mu3Lab keeps trying.',
  waiting_for_account:
    'Waiting for them to create their Vaultwarden account with the same email as their Mu3Lab sign-in. Their logins are saved as soon as they do.',
  skipped: 'Not saved: this account has no email or has been removed.',
};

/**
 * Mu3Lab saves the logins it creates into each person's vault by itself: into a
 * "Mu3Lab" collection only they can see. Bitwarden then fills them in.
 */
export function VaultSetupCard({ onSaved }: { onSaved?: () => void }) {
  const { data } = useDashboard();
  const [open, setOpen] = useState(false);
  const { pending, run } = useAction();
  const status = useApi<VaultStatus>('/api/v1/vault/status', { interval: 30000 });
  const automatic = status.data?.automatic;
  const people = automatic?.people || [];
  const saveNow = async () => {
    const saved = await run('sync', () => postApi('/api/v1/vault/sync'), 'Logins saved to Vaultwarden');
    if (saved) {
      void status.reload();
      onSaved?.();
    }
  };
  return (
    <Card
      title="Password vault"
      description="Mu3Lab saves the logins it creates for each person into their own Vaultwarden vault, in a collection called Mu3Lab that only they can see. The Bitwarden app or browser extension then fills them in, including for apps that ask for their own login after Authentik."
      actions={
        <Button icon={Vault} loading={pending === 'sync'} onClick={() => void saveNow()}>
          Save now
        </Button>
      }
    >
      {automatic?.error && (
        <Callout tone="warning" title="Saving is paused">
          {automatic.error} Mu3Lab tries again every few minutes.
        </Callout>
      )}
      {people.length > 0 ? (
        <div className="rows compact">
          {people.map((person) => (
            <div className="row" key={person.uid}>
              <span className="row-text">
                <b>{person.uid === data.identity.subject_id ? 'You' : person.name}</b>
                <small>{PERSON_STATE[person.state] || 'Not checked yet.'}</small>
              </span>
              {person.state === 'up_to_date' && <CheckCircle2 aria-label="Saved" className="ok-icon" />}
            </div>
          ))}
        </div>
      ) : (
        <p className="muted">
          {automatic?.last_run ? 'Nothing to save yet.' : 'Mu3Lab saves new logins within a few minutes of an install.'}
        </p>
      )}
      <p className="muted">
        Prefer your personal vault?{' '}
        <button type="button" className="link-button" onClick={() => setOpen(true)}>
          Save them there with your master password instead
        </button>
        .
      </p>
      {open && <VaultSetupDialog open onClose={() => setOpen(false)} onSaved={onSaved} />}
    </Card>
  );
}
