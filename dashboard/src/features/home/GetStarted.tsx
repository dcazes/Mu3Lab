import { ArrowRight, CheckCircle2, Circle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { putJsonApi, type ChecklistResponse, type ProviderMetadataResponse, type VaultStatus } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { Dialog } from '../../components/Dialog';
import { Card, PageHeader } from '../../components/Layout';
import { Link } from '../../lib/router';
import { isInstalled, launchTarget } from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

const EXTENSION_URL = 'https://bitwarden.com/download/#downloads-web-browser';
const TAILSCALE_DOWNLOAD_URL = 'https://tailscale.com/download';
const STORAGE_KEY = 'mu3lab.getStarted';

type Guide = 'extension' | 'devices';

export function useGetStartedChecklist() {
  const { data: checklist, reload } = useApi<ChecklistResponse>('/api/v1/me/checklist');
  const migrating = useRef(false);
  useEffect(() => {
    if (!checklist || migrating.current) return;
    migrating.current = true;
    const migrate = async () => {
      let saved: Record<string, unknown>;
      try {
        saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      } catch {
        localStorage.removeItem(STORAGE_KEY);
        return;
      }
      if (!saved || typeof saved !== 'object') return;
      const items = Object.fromEntries(
        Object.entries(saved).filter(
          ([key, done]) => ['devices', 'extension', 'chat', 'hidden'].includes(key) && done === true,
        ),
      );
      if (Object.keys(items).length) await putJsonApi('/api/v1/me/checklist', { items });
      localStorage.removeItem(STORAGE_KEY);
      if (Object.keys(items).length) await reload();
    };
    void migrate()
      .catch(() => toast.error('Could not save your checklist progress. Mu3Lab will retry.'))
      .finally(() => {
        migrating.current = false;
      });
  }, [checklist, reload]);
  const setItem = (key: string, value: boolean) => {
    void putJsonApi('/api/v1/me/checklist', { items: { [key]: value } })
      .then(() => reload())
      .catch(() => toast.error('Could not save checklist progress. Try again.'));
  };
  return [checklist?.items || {}, (key: string) => setItem(key, true), setItem] as const;
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

/** The steps for this person, or null while they load (or for people who can't act on them). */
function useGetStarted() {
  const { data } = useDashboard();
  const operator = data.identity.writes_enabled;
  const providers = useApi<ProviderMetadataResponse>(operator ? '/api/v1/providers' : null);
  const vault = useApi<VaultStatus>(operator ? '/api/v1/vault/status' : null);
  const [manual, mark, setItem] = useGetStartedChecklist();
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
  const setup = providers.data?.setup;
  if (!operator || !setup || !vault.data) return null;
  // Only apps the owner chose count; setup installs the core ones by itself.
  const hasApp = data.services.services.some(
    (service) => service.group === 'apps' && service.stage === 'optional' && isInstalled(service),
  );
  const extension = vault.data.browser_extension;
  const preinstalled = Boolean(extension?.browsers.length);
  const steps: Step[] = [
    {
      key: 'extension',
      text: preinstalled
        ? 'Sign in to Bitwarden so your browser fills in your passwords'
        : 'Add Bitwarden so your browser fills in your passwords',
      hint: preinstalled
        ? 'Click the shield icon next to the address bar, then sign in with your Mu3Lab email and password.'
        : '',
      done: Boolean(extension?.signed_in),
      guide: 'extension',
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
    { key: 'devices', text: 'Use Mu3Lab on your phone or laptop', done: Boolean(manual.devices), guide: 'devices' },
    { key: 'chat', text: 'Start chatting with your AI', done: Boolean(manual.chat), to: '/chat' },
  ];
  const finished = steps.filter((step) => step.done).length;
  return {
    steps,
    finished,
    complete: finished === steps.length,
    hidden: Boolean(manual.hidden),
    setHidden: (hidden: boolean) => setItem('hidden', hidden),
    markDone: mark,
    checking,
    checkExtension,
    browsers: extension?.browsers || [],
  };
}

interface Step {
  key: string;
  text: string;
  hint?: string;
  done: boolean;
  guide?: Guide;
  to?: string;
}

function StepList({
  state,
  compact = false,
}: {
  state: NonNullable<ReturnType<typeof useGetStarted>>;
  compact?: boolean;
}) {
  const [guide, setGuide] = useState<Guide | null>(null);
  const { steps, markDone, checking, checkExtension } = state;
  const next = steps.find((step) => !step.done)?.key;
  return (
    <>
      <ol className={`get-started-list${compact ? ' is-compact' : ''}`}>
        {steps.map((step, index) => {
          const content = (
            <>
              {step.done ? <CheckCircle2 className="get-started-done" /> : <Circle />}
              <span>
                {index + 1}. {step.text}
                {!step.done && !compact && step.hint && <small className="get-started-hint">{step.hint}</small>}
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
                <button type="button" className={className} onClick={() => setGuide(step.guide!)}>
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
      {guide === 'extension' && <ExtensionGuide browsers={state.browsers} onClose={() => setGuide(null)} />}
      {guide === 'devices' && <DevicesGuide onClose={() => setGuide(null)} onDone={() => markDone('devices')} />}
    </>
  );
}

/** Home's checklist; once hidden it leaves a small button to bring it back. */
export function GetStarted() {
  const state = useGetStarted();
  if (!state || state.complete) return null;
  if (state.hidden)
    return (
      <button type="button" className="get-started-show" onClick={() => state.setHidden(false)}>
        <Circle />
        Show Get started · {state.finished} of {state.steps.length} complete
      </button>
    );
  return (
    <section className="get-started" aria-labelledby="get-started-heading">
      <div className="section-title">
        <h2 id="get-started-heading">Get started</h2>
        <span className="get-started-count">
          {state.finished} of {state.steps.length} complete
        </span>
      </div>
      <StepList state={state} />
      <button type="button" className="get-started-hide" onClick={() => state.setHidden(true)}>
        Hide this list
      </button>
    </section>
  );
}

/** Settings > Get started: the same steps, compact, with the Home switch. */
export function GetStartedSettings() {
  const state = useGetStarted();
  return (
    <>
      <PageHeader title="Get started" description="The first steps for using Mu3Lab, and whether Home shows them." />
      {!state ? (
        <p className="muted">Checking your progress…</p>
      ) : (
        <Card
          title={state.complete ? 'All done' : `${state.finished} of ${state.steps.length} complete`}
          description={state.complete ? 'Every step is complete, so Home no longer shows this list.' : undefined}
        >
          <StepList state={state} compact />
          {!state.complete && (
            <label className="switch-field">
              <span>Show on Home</span>
              <input
                type="checkbox"
                role="switch"
                checked={!state.hidden}
                onChange={(event) => state.setHidden(!event.target.checked)}
              />
            </label>
          )}
        </Card>
      )}
    </>
  );
}
