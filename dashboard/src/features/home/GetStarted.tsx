import { ArrowRight, CheckCircle2, Circle } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import type { ProviderMetadataResponse, VaultStatus } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { Dialog } from '../../components/Dialog';
import { Link } from '../../lib/router';
import { isInstalled, launchTarget } from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

const EXTENSION_URL = 'https://bitwarden.com/download/#downloads-web-browser';
const TAILSCALE_DOWNLOAD_URL = 'https://tailscale.com/download';
const STORAGE_KEY = 'mu3lab.getStarted';

type Guide = 'extension' | 'devices';

function useManualDone() {
  const read = () => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') as Record<string, boolean>;
    } catch {
      return {};
    }
  };
  const [done, setDone] = useState<Record<string, boolean>>(read);
  const mark = (key: string) => {
    const next = { ...read(), [key]: true };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    setDone(next);
  };
  return [done, mark] as const;
}

function ExtensionGuide({ browsers, onClose }: { browsers: string[]; onClose: () => void }) {
  const { data } = useDashboard();
  const vaultwarden = data.services.services.find((service) => service.id === 'vaultwarden');
  const vaultUrl = (vaultwarden && launchTarget(vaultwarden)?.url) || '';
  const preinstalled = browsers.length > 0;
  return (
    <Dialog
      open
      onClose={onClose}
      title="Set up password autofill"
      description="Use the Bitwarden extension to fill logins saved in your Vaultwarden vault."
    >
      <div className="form">
        {preinstalled ? (
          <ol className="guide-steps">
            <li>
              Setup already added Bitwarden to {browsers.join(' and ')} and connected it to your vault. If you
              don&apos;t see it, close the browser and open it again.
            </li>
            <li>Click the Bitwarden shield icon next to the address bar.</li>
            <li>Sign in with your Vaultwarden email and master password.</li>
          </ol>
        ) : (
          <>
            <ol className="guide-steps">
              <li>
                Click <b>Get the extension</b> below and add Bitwarden to your browser.
              </li>
              <li>Click the Bitwarden icon in your browser's toolbar (it looks like a shield).</li>
              <li>
                On its sign-in screen, find <b>Logging in on</b> (or <b>Accessing</b>) and choose <b>Self-hosted</b>.
              </li>
              <li>Paste this address into the Server URL box and save:</li>
            </ol>
            {vaultUrl && <CopyField value={vaultUrl} label="Server URL" />}
            <ol className="guide-steps" start={5}>
              <li>Sign in with your Vaultwarden email and master password.</li>
            </ol>
          </>
        )}
        <footer className="form-footer">
          {!preinstalled && <ExternalButton href={EXTENSION_URL}>Get the extension</ExternalButton>}
          <span className="spacer" />
          <span className="guide-note">
            This step is marked complete when Mu3Lab detects a signed-in Bitwarden extension.
          </span>
          <Button variant="primary" onClick={onClose}>
            Close
          </Button>
        </footer>
      </div>
    </Dialog>
  );
}

function DevicesGuide({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  return (
    <Dialog
      open
      onClose={onClose}
      title="Use Mu3Lab on your phone or laptop"
      description="Access Mu3Lab from devices authorized on your Tailscale network."
    >
      <div className="form">
        <ol className="guide-steps">
          <li>
            On your phone or other computer, install the <b>Tailscale</b> app.
          </li>
          <li>Sign in to the Tailscale network used by your Mu3Lab server and connect the device.</li>
          <li>Open this address in that device's browser (bookmark it):</li>
        </ol>
        <CopyField value={window.location.origin + '/'} label="Dashboard address" />
        <footer className="form-footer">
          <ExternalButton href={TAILSCALE_DOWNLOAD_URL}>Get Tailscale</ExternalButton>
          <span className="spacer" />
          <Button
            variant="primary"
            onClick={() => {
              onDone();
              onClose();
            }}
          >
            Mark as complete
          </Button>
        </footer>
      </div>
    </Dialog>
  );
}

export function GetStarted() {
  const { data } = useDashboard();
  const operator = data.identity.writes_enabled;
  const providers = useApi<ProviderMetadataResponse>(operator ? '/api/v1/providers' : null, { interval: 60000 });
  const vault = useApi<VaultStatus>(operator ? '/api/v1/vault/status' : null, { interval: 60000 });
  const [manual, markDone] = useManualDone();
  const [checking, setChecking] = useState(false);
  const checkExtension = async () => {
    setChecking(true);
    try {
      const status = await vault.reload();
      if (!status) toast.error('Could not check Bitwarden login. Try again.');
      else if (status.browser_extension?.signed_in) toast.success('Bitwarden login confirmed');
      else toast.info('No signed-in Bitwarden extension detected yet. Sign in, then check again.');
    } catch {
      toast.error('Could not check Bitwarden login. Try again.');
    } finally {
      setChecking(false);
    }
  };
  const [guide, setGuide] = useState<Guide | null>(null);
  const setup = providers.data?.setup;
  if (!operator || !setup || !vault.data || manual.hidden) return null;
  // Only apps the owner chose count; setup installs the core ones by itself.
  const hasApp = data.services.services.some(
    (service) => service.group === 'apps' && service.stage === 'optional' && isInstalled(service),
  );
  const extension = vault.data.browser_extension;
  const preinstalled = Boolean(extension?.browsers.length);
  const steps = [
    {
      key: 'extension',
      text: preinstalled
        ? 'Sign in to Bitwarden so your browser fills in your passwords'
        : 'Add Bitwarden so your browser fills in your passwords',
      hint: preinstalled
        ? 'Click the shield icon next to the address bar, then sign in with your Mu3Lab email and password.'
        : '',
      done: Boolean(extension?.signed_in),
      guide: 'extension' as const,
    },
    // Mu3Lab saves generated logins to the vault by itself; only a stalled save needs the owner.
    ...(vault.data.automatic?.error
      ? [{ key: 'vault', text: 'Check why your app logins are not being saved', done: false, to: '/settings/sign-in' }]
      : []),
    // Connecting AI providers is administration; members skip it.
    ...(data.identity.is_admin
      ? [{ key: 'provider', text: 'Connect an AI provider for chat', done: setup.complete, to: '/settings/ai' }]
      : []),
    { key: 'app', text: 'Install your first personal app', done: hasApp, to: '/apps/discover' },
    {
      key: 'devices',
      text: 'Use Mu3Lab on your phone or laptop',
      done: Boolean(manual.devices),
      guide: 'devices' as const,
    },
    { key: 'chat', text: 'Start chatting with your AI in LobeChat', done: Boolean(manual.chat), to: '/chat' },
  ];
  const finished = steps.filter((step) => step.done).length;
  if (finished === steps.length) return null;
  const next = steps.find((step) => !step.done)?.key;
  return (
    <section className="get-started" aria-labelledby="get-started-heading">
      <div className="section-title">
        <h2 id="get-started-heading">Get started</h2>
        <span className="get-started-count">
          {finished} of {steps.length} complete
        </span>
      </div>
      <ol className="get-started-list">
        {steps.map((step, index) => {
          const content = (
            <>
              {step.done ? <CheckCircle2 className="get-started-done" /> : <Circle />}
              <span>
                {index + 1}. {step.text}
                {!step.done && 'hint' in step && step.hint && <small className="get-started-hint">{step.hint}</small>}
              </span>
              {!step.done && <ArrowRight className="attention-arrow" />}
            </>
          );
          const className = `get-started-item${step.done ? ' is-done' : ''}${step.key === next ? ' is-next' : ''}`;
          return (
            <li key={step.key}>
              {step.done ? (
                <div className={className}>{content}</div>
              ) : step.key === 'extension' ? (
                <div className={`${className} get-started-extension`}>
                  <button type="button" className="get-started-guide" onClick={() => setGuide('extension')}>
                    {content}
                  </button>
                  <Button size="sm" loading={checking} onClick={() => void checkExtension()}>
                    Check Bitwarden login
                  </Button>
                </div>
              ) : step.guide ? (
                <button type="button" className={className} onClick={() => setGuide(step.guide)}>
                  {content}
                </button>
              ) : (
                <Link
                  className={className}
                  to={step.to!}
                  onClick={step.key === 'chat' ? () => markDone('chat') : undefined}
                >
                  {content}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
      <button type="button" className="get-started-hide" onClick={() => markDone('hidden')}>
        Hide this list
      </button>
      {guide === 'extension' && <ExtensionGuide browsers={extension?.browsers || []} onClose={() => setGuide(null)} />}
      {guide === 'devices' && <DevicesGuide onClose={() => setGuide(null)} onDone={() => markDone('devices')} />}
    </section>
  );
}
