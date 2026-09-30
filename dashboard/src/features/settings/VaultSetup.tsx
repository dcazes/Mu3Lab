import { CheckCircle2, Vault } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { ApiError, postJsonApi, type VaultSetupResult } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { Dialog } from '../../components/Dialog';
import { Callout, Card } from '../../components/Layout';
import { errorText } from '../../lib/format';
import { launchTarget } from '../../lib/services';
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

/** Settings card that opens the one-time vault setup. Safe to run again at any time. */
export function VaultSetupCard({ onSaved }: { onSaved?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Card
      title="Password vault"
      description="Save generated app credentials and suggested AI provider registration entries to Vaultwarden for browser autofill. Existing entries are preserved or updated when generated credentials change."
      actions={
        <Button variant="primary" icon={Vault} onClick={() => setOpen(true)}>
          Save logins to Vaultwarden
        </Button>
      }
    >
      {open && <VaultSetupDialog open onClose={() => setOpen(false)} onSaved={onSaved} />}
    </Card>
  );
}
