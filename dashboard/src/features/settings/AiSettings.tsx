import { CheckCircle2, ClipboardPaste, KeyRound, LogIn, Pencil, Power, Sparkles, Trash2 } from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import {
  deleteApi,
  type JobDetailResponse,
  postApi,
  postJsonApi,
  type ProviderCatalogItem,
  type ProviderMetadata,
  type ProviderMetadataResponse,
  type ProviderSetupProgress,
} from '../../api';
import { AppIcon } from '../../components/AppIcon';
import { Button, ExternalButton } from '../../components/Button';
import { Dialog, useConfirm } from '../../components/Dialog';
import { Callout, Card, Collapsible, EmptyState, PageHeader } from '../../components/Layout';
import { Menu } from '../../components/Menu';
import { Badge, Dot, StateBadge } from '../../components/Status';
import { humanize, relativeTime } from '../../lib/format';
import { Link } from '../../lib/router';
import { isRunning, stateLabel } from '../../lib/services';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';
import { detectProvider, openRouterSignInUrl, takeOpenRouterReturn } from './providerKeys';

const ROUTE = [
  { id: 'ollama', label: 'Ollama', role: 'Local models & embeddings' },
  { id: 'freellmapi', label: 'FreeLLMAPI', role: 'Your provider accounts' },
  { id: 'litellm', label: 'LiteLLM', role: 'One private gateway' },
  { id: 'lobehub', label: 'LobeChat', role: 'Chat' },
];

function ChatRoute() {
  const { data } = useDashboard();
  return (
    <Card
      title="How chat is routed"
      description="A route counts as ready only after streamed chat and embedding checks pass."
    >
      <ol className="route">
        {ROUTE.map((step) => {
          const service = data.services.services.find((item) => item.id === step.id);
          const ready = service && isRunning(service);
          return (
            <li key={step.id}>
              <Link to={`/apps/${step.id}`} className="route-step">
                <AppIcon id={step.id} size="sm" />
                <span>
                  <b>{step.label}</b>
                  <small>{step.role}</small>
                </span>
                <span className="route-state">
                  <Dot tone={ready ? 'green' : 'gray'} />
                  {service ? stateLabel[service.state] : 'Unavailable'}
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function VerificationJob({ id }: { id: string }) {
  const job = useApi<JobDetailResponse>(`/api/v1/jobs/${id}`, { interval: 4000 });
  if (!job.data) return null;
  return (
    <Collapsible title={`Verification: ${humanize(job.data.job.step_id || job.data.job.state)}`}>
      <pre className="log">
        {job.data.events
          .slice(-8)
          .map((event) => event.detail)
          .join('\n')}
      </pre>
    </Collapsible>
  );
}

function ProviderDialog({
  open,
  onClose,
  catalog,
  providers,
  initialId,
  saved,
}: {
  open: boolean;
  onClose: () => void;
  catalog: ProviderCatalogItem[];
  providers: ProviderMetadata[];
  initialId: string;
  saved: () => void;
}) {
  const [providerId, setProviderId] = useState(initialId);
  const [label, setLabel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const { pending, run } = useAction();
  const selected = catalog.find((item) => item.id === providerId);
  const existing = providers.find((item) => item.id === providerId);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const result = await run(
      'save',
      () => postJsonApi<{ warning?: string }>('/api/v1/providers', { provider_id: providerId, label, api_key: apiKey }),
      (response) => response.warning || 'Saved. Mu3Lab is verifying the connection now.',
    );
    if (result) {
      setApiKey('');
      saved();
      onClose();
    }
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={existing ? `Replace ${existing.name} key` : 'Add a provider'}
      description="Keys are encrypted on this server and never shown again. Prompts sent to an external provider leave your home server."
    >
      <form className="form" onSubmit={submit}>
        <label className="field">
          <span>Provider</span>
          <select required value={providerId} onChange={(event) => setProviderId(event.target.value)}>
            <option value="">Choose a provider…</option>
            {catalog.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        {selected && (
          <p className="hint">
            <KeyRound />
            <span>
              {selected.instructions} <code>{selected.key_hint}</code>
              {selected.keys_url && (
                <>
                  {' '}
                  <a href={selected.keys_url} target="_blank" rel="noreferrer">
                    Open the API key page
                  </a>
                </>
              )}
            </span>
          </p>
        )}
        <label className="field">
          <span>
            Name <em>optional</em>
          </span>
          <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder={selected?.name || ''} />
        </label>
        <label className="field">
          <span>API key</span>
          <input
            required
            type="password"
            autoComplete="new-password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={selected?.key_hint || ''}
          />
        </label>
        <footer className="form-footer">
          <span className="spacer" />
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" loading={pending === 'save'} disabled={!providerId}>
            {existing ? 'Replace and verify' : 'Save and verify'}
          </Button>
        </footer>
      </form>
    </Dialog>
  );
}

function ProviderRow({
  provider,
  act,
  replace,
}: {
  provider: ProviderMetadata;
  act: (provider: ProviderMetadata, action: 'verify' | 'enable' | 'disable' | 'remove') => void;
  replace: () => void;
}) {
  return (
    <div className="provider">
      <div className="row">
        <span className="row-text">
          <b>{provider.name}</b>
          <small>
            {provider.label !== provider.name ? provider.label : provider.credential_indicator}
            {' · '}
            {provider.last_verified_at
              ? `verified ${relativeTime(provider.last_verified_at)}`
              : `saved ${relativeTime(provider.updated_at)}`}
          </small>
        </span>
        <span className="chips hide-mobile">
          {provider.model_samples.slice(0, 3).map((model) => (
            <span className="chip" key={model}>
              {model}
            </span>
          ))}
        </span>
        <StateBadge state={provider.state} />
        <Menu
          label={`${provider.name} actions`}
          items={[
            ...(provider.supported
              ? [
                  { label: 'Verify again', icon: CheckCircle2, onSelect: () => act(provider, 'verify') },
                  {
                    label: provider.enabled ? 'Disable' : 'Enable',
                    icon: Power,
                    onSelect: () => act(provider, provider.enabled ? 'disable' : 'enable'),
                  },
                ]
              : []),
            { label: 'Replace key', icon: Pencil, onSelect: replace },
            { label: 'Remove', icon: Trash2, danger: true, onSelect: () => act(provider, 'remove') },
          ]}
        />
      </div>
      {provider.error && (
        <p className="error-text provider-error">
          {provider.error} {provider.recommended_action}
        </p>
      )}
      {provider.active_job_id && provider.state === 'verifying' && <VerificationJob id={provider.active_job_id} />}
    </div>
  );
}

function KeyPaste({
  catalog,
  providers,
  awaiting,
  clearAwaiting,
  saved,
}: {
  catalog: ProviderCatalogItem[];
  providers: ProviderMetadata[];
  awaiting: string;
  clearAwaiting: () => void;
  saved: () => void;
}) {
  const [apiKey, setApiKey] = useState('');
  const [choice, setChoice] = useState('');
  const { pending, run } = useAction();
  const submitted = useRef('');
  const detection = detectProvider(apiKey, catalog);
  const providerId = choice || detection?.provider.id || '';
  const provider = catalog.find((item) => item.id === providerId);
  const replacing = providers.some((item) => item.id === providerId);

  const connect = useCallback(
    async (key: string, id: string) => {
      const name = catalog.find((item) => item.id === id)?.name || 'Provider';
      submitted.current = key;
      const result = await run(
        'save',
        () => postJsonApi<{ warning?: string }>('/api/v1/providers', { provider_id: id, api_key: key }),
        (response) => response.warning || `${name} key saved. Mu3Lab is verifying it now.`,
      );
      if (result) {
        setApiKey('');
        setChoice('');
        clearAwaiting();
        saved();
      }
    },
    [catalog, clearAwaiting, run, saved],
  );

  const take = useCallback(
    (text: string) => {
      const key = text.trim();
      setApiKey(key);
      setChoice('');
      const found = detectProvider(key, catalog);
      // A distinctive prefix is certain enough to connect straight away.
      if (found?.certain && key !== submitted.current) void connect(key, found.provider.id);
    },
    [catalog, connect],
  );

  const paste = async () => {
    try {
      take(await navigator.clipboard.readText());
    } catch {
      toast.error('Your browser blocked reading the clipboard. Click the box and press Ctrl+V instead.');
    }
  };

  // After "Get key", coming back to this tab picks the copied key up by itself
  // once the browser has allowed clipboard access (it asks the first time).
  useEffect(() => {
    if (!awaiting) return;
    const onFocus = async () => {
      try {
        const permission = await navigator.permissions.query({ name: 'clipboard-read' as PermissionName });
        if (permission.state !== 'granted') return;
        const key = (await navigator.clipboard.readText()).trim();
        const found = detectProvider(key, catalog);
        if (found?.certain && found.provider.id === awaiting && key !== submitted.current) take(key);
      } catch {
        // No clipboard access: the Paste button still works.
      }
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [awaiting, catalog, take]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (apiKey && providerId) void connect(apiKey.trim(), providerId);
  };

  return (
    <form className="key-paste" onSubmit={submit}>
      <div className="key-paste-row">
        <input
          aria-label="API key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={apiKey}
          onChange={(event) => {
            setApiKey(event.target.value);
            setChoice('');
          }}
          onPaste={(event) => {
            event.preventDefault();
            take(event.clipboardData.getData('text'));
          }}
          placeholder="Paste any provider's API key"
        />
        <Button icon={ClipboardPaste} onClick={() => void paste()} loading={pending === 'save' && !apiKey}>
          Paste
        </Button>
        <Button variant="primary" type="submit" loading={pending === 'save' && !!apiKey} disabled={!providerId}>
          {replacing ? 'Replace key' : 'Connect'}
        </Button>
      </div>
      {awaiting && !apiKey && (
        <p className="key-paste-note">
          Copied your {catalog.find((item) => item.id === awaiting)?.name} key? Come back here and click <b>Paste</b>.
        </p>
      )}
      {apiKey && (
        <div className="key-paste-note">
          {detection?.certain && !choice ? (
            <span>
              <Badge tone="green">
                <CheckCircle2 />
                {detection.provider.name} key
              </Badge>
            </span>
          ) : (
            <label className="key-paste-choice">
              <span>
                {detection
                  ? `Looks like a ${detection.provider.name} key. Not right? Choose:`
                  : 'Which provider is this key for?'}
              </span>
              <select value={providerId} onChange={(event) => setChoice(event.target.value)}>
                <option value="">Choose a provider…</option>
                {catalog.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {provider && replacing && <span> This replaces your saved {provider.name} key.</span>}
        </div>
      )}
    </form>
  );
}

function OpenRouterConnect() {
  const { pending, run } = useAction();
  return (
    <Button
      size="sm"
      variant="primary"
      icon={LogIn}
      loading={pending === 'oauth'}
      onClick={() =>
        void run('oauth', async () => {
          window.location.assign(await openRouterSignInUrl());
        })
      }
    >
      Connect with OpenRouter
    </Button>
  );
}

function ProviderChecklist({
  catalog,
  providers,
  setup,
  saved,
}: {
  catalog: ProviderCatalogItem[];
  providers: ProviderMetadata[];
  setup?: ProviderSetupProgress;
  saved: () => void;
}) {
  const [awaiting, setAwaiting] = useState('');
  const clearAwaiting = useCallback(() => setAwaiting(''), []);
  if (!catalog.length) return null;
  const minimum = setup?.recommended_minimum ?? 2;
  const done = setup?.recommended_verified.length ?? 0;
  const byId = new Map(providers.map((provider) => [provider.id, provider]));
  const ordered = [...catalog].sort((a, b) => Number(!!b.recommended) - Number(!!a.recommended));
  return (
    <Card
      title={setup?.recommendation_met ? 'More free providers' : 'Get free AI capacity'}
      description={`Free tiers have daily limits. Connect at least ${minimum} recommended providers so chat can switch when one runs out — ${Math.min(done, minimum)} of ${minimum} done.`}
      flush
    >
      {setup && !setup.complete && (
        <Callout tone="info" icon={Sparkles} title="Connect one provider to finish setup">
          Any provider works. The recommended ones have the most generous free tiers.
        </Callout>
      )}
      {setup?.complete && !setup.recommendation_met && (
        <Callout tone="warning" icon={Sparkles} title="Chat works — add a backup provider">
          With only one recommended provider, chat stops when it reaches its free limit for the day.
        </Callout>
      )}
      <KeyPaste
        catalog={catalog}
        providers={providers}
        awaiting={awaiting}
        clearAwaiting={clearAwaiting}
        saved={saved}
      />
      <div className="rows">
        {ordered.map((item) => {
          const connection = byId.get(item.id);
          const verified = connection?.enabled && connection.state === 'verified';
          return (
            <div className="row" key={item.id}>
              <span className="row-text">
                <b>
                  {item.name} {item.recommended && <Badge tone="blue">Recommended</Badge>}{' '}
                  {item.google_sign_in && <Badge>One-click Google sign-in</Badge>}
                </b>
                <small>{item.free_tier}</small>
              </span>
              <span className="row-actions">
                {connection ? (
                  verified ? (
                    <Badge tone="green">
                      <Dot tone="green" />
                      Connected
                    </Badge>
                  ) : (
                    <StateBadge state={connection.state} />
                  )
                ) : item.oauth ? (
                  <OpenRouterConnect />
                ) : (
                  <>
                    {item.signup_url && (
                      <ExternalButton size="sm" href={item.signup_url} className="hide-mobile">
                        Sign up
                      </ExternalButton>
                    )}
                    {item.keys_url && (
                      <ExternalButton size="sm" href={item.keys_url} onClick={() => setAwaiting(item.id)}>
                        Get key
                      </ExternalButton>
                    )}
                  </>
                )}
              </span>
            </div>
          );
        })}
      </div>
      <p className="hint">
        <KeyRound />
        <span>
          Rather sign up with email? Your vault has a ready-made login with a unique password for each provider in the{' '}
          <b>Mu3Lab/AI providers</b> folder, and Bitwarden offers it on the sign-up page.{' '}
          <Link to="/settings/sign-in">Create them</Link> if you haven&apos;t yet.
        </span>
      </p>
    </Card>
  );
}

export function AiSettings() {
  const confirm = useConfirm();
  const { run } = useAction();
  const providers = useApi<ProviderMetadataResponse>('/api/v1/providers', { interval: 5000 });
  const catalog = useApi<{ providers: ProviderCatalogItem[] }>('/api/v1/providers/catalog');
  const [dialog, setDialog] = useState<{ id: string } | null>(null);
  const list = providers.data?.providers || [];
  const reload = providers.reload;

  // OpenRouter sends the owner back here with a one-time code after sign-in.
  useEffect(() => {
    const back = takeOpenRouterReturn();
    if (!back) return;
    if (!back.code || !back.code_verifier) {
      toast.error('The OpenRouter sign-in did not finish. Click Connect with OpenRouter to try again.');
      return;
    }
    void run(
      'oauth',
      () => postJsonApi('/api/v1/providers/openrouter/oauth', back),
      'OpenRouter connected. Mu3Lab is verifying it now.',
    ).then(() => reload());
  }, [run, reload]);

  const act = async (provider: ProviderMetadata, action: 'verify' | 'enable' | 'disable' | 'remove') => {
    if (
      ['remove', 'disable'].includes(action) &&
      !(await confirm({
        title: `${action === 'remove' ? 'Remove' : 'Disable'} ${provider.name}?`,
        description: action === 'remove' ? 'Its encrypted key is deleted from this server.' : undefined,
        confirmLabel: action === 'remove' ? 'Remove' : 'Disable',
        tone: 'danger',
      }))
    )
      return;
    await run(
      action,
      () =>
        action === 'remove'
          ? deleteApi(`/api/v1/providers/${provider.id}`)
          : postApi(`/api/v1/providers/${provider.id}/${action}`),
      `${provider.name}: ${action === 'verify' ? 'verification started' : `${action}d`}`,
    );
    window.setTimeout(() => void providers.reload(), 800);
  };

  return (
    <>
      <PageHeader
        title="AI providers"
        description="Connect the model providers you want chat to use. Free tiers are never guaranteed."
      />
      <Card title="Connected providers" flush>
        {providers.error ? (
          <p className="error-text card-pad">Could not load providers: {providers.error}</p>
        ) : list.length ? (
          <div className="rows">
            {list.map((provider) => (
              <ProviderRow
                key={provider.id}
                provider={provider}
                act={(item, action) => void act(item, action)}
                replace={() => setDialog({ id: provider.id })}
              />
            ))}
          </div>
        ) : (
          <EmptyState icon={KeyRound} title="No providers yet">
            Local models work without one. Connect a provider below for larger hosted models.
          </EmptyState>
        )}
      </Card>
      <ProviderChecklist
        catalog={catalog.data?.providers || []}
        providers={list}
        setup={providers.data?.setup}
        saved={() => void reload()}
      />
      <ChatRoute />
      {dialog && (
        <ProviderDialog
          key={dialog.id}
          open
          onClose={() => setDialog(null)}
          catalog={catalog.data?.providers || []}
          providers={list}
          initialId={dialog.id}
          saved={() => void reload()}
        />
      )}
    </>
  );
}
