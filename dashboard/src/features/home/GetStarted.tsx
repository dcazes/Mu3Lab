import { ArrowRight, CheckCircle2, Circle } from 'lucide-react';
import { useState } from 'react';
import type { ProviderMetadataResponse, VaultStatus } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { Dialog } from '../../components/Dialog';
import { Link } from '../../lib/router';
import { displayStage, isInstalled, launchTarget } from '../../lib/services';
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
      title="Let your browser fill in your passwords"
      description="Mu3Lab saved every app login in your password vault. The free Bitwarden extension fills them in for you."
    >
      <div className="form">
        {preinstalled ? (
          <ol className="guide-steps">
            <li>
              Setup already added Bitwarden to {browsers.join(' and ')} and connected it to your vault. If you
              don&apos;t see it, close the browser and open it again.
            </li>
            <li>Click the Bitwarden shield icon next to the address bar.</li>
            <li>Sign in with your Mu3Lab email and password.</li>
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
              <li>Sign in with your Mu3Lab email and password.</li>
            </ol>
          </>
        )}
        <footer className="form-footer">
          {!preinstalled && <ExternalButton href={EXTENSION_URL}>Get the extension</ExternalButton>}
          <span className="spacer" />
          <span className="guide-note">This step ticks itself off once Bitwarden signs in.</span>
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
      description="Mu3Lab is private: it only opens on your own devices that are signed in to Tailscale."
    >
      <div className="form">
        <ol className="guide-steps">
          <li>
            On your phone or other computer, install the free <b>Tailscale</b> app.
          </li>
          <li>Open it and sign in with the same account you used while installing Mu3Lab.</li>
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
            I've done this
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
  const [guide, setGuide] = useState<Guide | null>(null);
  const setup = providers.data?.setup;
  if (!operator || !setup || !vault.data || manual.hidden) return null;
  // Only apps the owner chose count; setup installs the core ones by itself.
  const hasApp = data.services.services.some((service) => displayStage(service) === 'optional' && isInstalled(service));
  const extension = vault.data.browser_extension;
  const preinstalled = Boolean(extension?.browsers.length);
  const steps = [
    {
      key: 'provider',
      text: 'Connect a free AI provider so the AI chat can answer',
      done: setup.complete,
      to: '/settings/ai',
    },
    ...(vault.data.seeded
      ? []
      : [{ key: 'vault', text: 'Save your app logins to your password vault', done: false, to: '/settings/sign-in' }]),
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
    {
      key: 'devices',
      text: 'Use Mu3Lab on your phone or laptop',
      done: Boolean(manual.devices),
      guide: 'devices' as const,
    },
    { key: 'app', text: 'Add your first app, like photos or recipes', done: hasApp, to: '/apps/discover' },
    {
      key: 'backup',
      text: 'Add a second free AI provider as a backup',
      done: setup.recommendation_met,
      to: '/settings/ai',
    },
  ];
  const finished = steps.filter((step) => step.done).length;
  if (finished === steps.length) return null;
  const next = steps.find((step) => !step.done)?.key;
  return (
    <section className="get-started" aria-labelledby="get-started-heading">
      <div className="section-title">
        <h2 id="get-started-heading">Get started</h2>
        <span className="get-started-count">
          {finished} of {steps.length} done
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
              ) : step.guide ? (
                <button type="button" className={className} onClick={() => setGuide(step.guide)}>
                  {content}
                </button>
              ) : (
                <Link className={className} to={step.to!}>
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
