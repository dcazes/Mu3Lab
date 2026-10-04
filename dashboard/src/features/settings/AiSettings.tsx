import { CheckCircle2, ClipboardCheck, ClipboardPaste, KeyRound, Pencil, Power, Sparkles, Trash2 } from 'lucide-react';
import { useIsAdmin } from '../../state/dashboard';
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
import { detectProvider, keyWarning } from './providerKeys';

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
                  {service ? stateLabel[service.display_state] : 'Unavailable'}
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
            className="key-input"
            autoComplete="off"
            spellCheck={false}
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
  readOnly = false,
}: {
  provider: ProviderMetadata;
  act: (provider: ProviderMetadata, action: 'verify' | 'enable' | 'disable' | 'remove') => void;
  replace: () => void;
  readOnly?: boolean;
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
        {!readOnly && (
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
        )}
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
  const [changing, setChanging] = useState(false);
  // True when the key came from the clipboard by itself, so the owner knows not to paste it again.
  const [pickedUp, setPickedUp] = useState(false);
  const { pending, run } = useAction();
  const submitted = useRef('');
  const key = apiKey.trim();
  const detection = detectProvider(key, catalog);
  const warning = keyWarning(key);
  const providerId = choice || detection?.provider.id || '';
  const provider = catalog.find((item) => item.id === providerId);
  const replacing = providers.some((item) => item.id === providerId);

  // Nothing is saved until the owner clicks Connect; any text is treated as a key.
  const take = useCallback((text: string, fromClipboard = false) => {
    setApiKey(text.trim());
    setChoice('');
    setChanging(false);
    setPickedUp(fromClipboard);
  }, []);

  const paste = async () => {
    try {
      take(await navigator.clipboard.readText());
    } catch {
      toast.error('Your browser blocked reading the clipboard. Click the box and press Ctrl+V instead.');
    }
  };

  // After "Get key", coming back to this tab fills the box with the copied key
  // once the browser has allowed clipboard access (it asks the first time).
  useEffect(() => {
    if (!awaiting) return;
    const onFocus = async () => {
      try {
        const permission = await navigator.permissions.query({ name: 'clipboard-read' as PermissionName });
        if (permission.state !== 'granted') return;
        const text = (await navigator.clipboard.readText()).trim();
        if (!text || text === apiKey || text === submitted.current) return;
        if (detectProvider(text, catalog)?.provider.id === awaiting) take(text, true);
      } catch {
        // No clipboard access: pasting still works.
      }
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [apiKey, awaiting, catalog, take]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!key || !provider) return;
    submitted.current = key;
    const result = await run(
      'save',
      () => postJsonApi<{ warning?: string }>('/api/v1/providers', { provider_id: provider.id, api_key: key }),
      (response) => response.warning || `${provider.name} key saved. Mu3Lab is verifying it now.`,
    );
    if (result) {
      take('');
      clearAwaiting();
      saved();
    }
  };

  return (
    <form className="key-paste" onSubmit={(event) => void submit(event)}>
      <div className="key-paste-row">
        <input
          aria-label="API key"
          className="key-input"
          autoComplete="off"
          spellCheck={false}
          value={apiKey}
          onChange={(event) => take(event.target.value)}
          onPaste={(event) => {
            event.preventDefault();
            take(event.clipboardData.getData('text'));
          }}
          placeholder="Paste a supported provider’s API key"
        />
        <Button icon={ClipboardPaste} onClick={() => void paste()}>
          Paste
        </Button>
        <Button variant="primary" type="submit" loading={pending === 'save'} disabled={!key || !provider}>
          {replacing ? 'Replace key' : 'Connect'}
        </Button>
      </div>
      {awaiting && !key && (
        <p className="key-paste-note">
          Copied your {catalog.find((item) => item.id === awaiting)?.name} key? Come back here and paste it.
        </p>
      )}
      {key && pickedUp && (
        <p className="key-paste-note is-success" role="status">
          <ClipboardCheck />
          Picked up your copied {provider?.name || ''} key from the clipboard. No need to paste it — check it and click{' '}
          <b>{replacing ? 'Replace key' : 'Connect'}</b>.
        </p>
      )}
      {key && warning && (
        <p className="key-paste-note is-warning" role="status">
          {warning}
        </p>
      )}
      {key && (
        <div className="key-paste-note key-paste-choice">
          {detection?.certain && !changing ? (
            <>
              <Badge tone="green">
                <CheckCircle2 />
                {detection.provider.name} key
              </Badge>
              <Button size="sm" variant="ghost" onClick={() => setChanging(true)}>
                Not {detection.provider.name}? Change
              </Button>
            </>
          ) : (
            <label className="key-paste-choice">
              <span>
                {detection && !changing
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
          {provider && replacing && <span>This replaces your saved {provider.name} key.</span>}
        </div>
      )}
    </form>
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
  // Anything already connected is listed under Connected providers instead.
  const ordered = catalog
    .filter((item) => !byId.has(item.id))
    .sort((a, b) => Number(!!b.recommended) - Number(!!a.recommended));
  return (
    <Card
      title={setup?.recommendation_met ? 'More free providers' : 'Get free AI capacity'}
      description={`Free-tier quotas and availability vary by provider. Connect ${minimum} recommended providers for fallback capacity — ${Math.min(done, minimum)} of ${minimum} connected.`}
      flush
    >
      {setup && !setup.complete && (
        <Callout tone="info" icon={Sparkles} title="Connect one provider to finish setup">
          Connect and verify a supported provider. Recommended providers are selected for their available free-tier
          capacity.
        </Callout>
      )}
      {setup?.complete && !setup.recommendation_met && (
        <Callout tone="warning" icon={Sparkles} title="Chat works — add a backup provider">
          Another verified provider can help keep chat available when a provider reaches its quota or is temporarily
          unavailable.
        </Callout>
      )}
      <KeyPaste
        catalog={catalog}
        providers={providers}
        awaiting={awaiting}
        clearAwaiting={clearAwaiting}
        saved={saved}
      />
      {ordered.length > 0 && (
        <div className="rows">
          {ordered.map((item) => (
            <div className="row" key={item.id}>
              <span className="row-text">
                <b>
                  {item.name} {item.recommended && <Badge tone="blue">Recommended</Badge>}{' '}
                  {item.payment_required && <Badge tone="amber">Payment method required</Badge>}
                </b>
                <small>{item.free_tier}</small>
              </span>
              <span className="row-actions">
                {item.keys_url && (
                  <ExternalButton size="sm" href={item.keys_url} onClick={() => setAwaiting(item.id)}>
                    Get key
                  </ExternalButton>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      <p className="hint">
        <KeyRound />
        <span>
          For email registration, Mu3Lab can save suggested logins with unique passwords in the{' '}
          <b>Mu3Lab/AI providers</b> folder in Vaultwarden. These entries help you register; they do not create provider
          accounts. <Link to="/settings/sign-in">Save registration entries</Link> if you haven&apos;t yet.
        </span>
      </p>
    </Card>
  );
}

export function AiSettings() {
  const isAdmin = useIsAdmin();
  const confirm = useConfirm();
  const { run } = useAction();
  const providers = useApi<ProviderMetadataResponse>('/api/v1/providers', { interval: 5000 });
  const catalog = useApi<{ providers: ProviderCatalogItem[] }>('/api/v1/providers/catalog');
  const [dialog, setDialog] = useState<{ id: string } | null>(null);
  const list = providers.data?.providers || [];
  const reload = providers.reload;

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
      {!isAdmin && (
        <Callout title="Only an administrator can change AI providers">
          You can see which providers chat uses. Ask an administrator to connect or change one.
        </Callout>
      )}
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
                readOnly={!isAdmin}
              />
            ))}
          </div>
        ) : (
          <EmptyState icon={KeyRound} title="No providers yet">
            Local models work without one. Connect a provider below for larger hosted models.
          </EmptyState>
        )}
      </Card>
      {isAdmin && (
        <ProviderChecklist
          catalog={catalog.data?.providers || []}
          providers={list}
          setup={providers.data?.setup || undefined}
          saved={() => void reload()}
        />
      )}
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
