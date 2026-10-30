import { RefreshCw } from 'lucide-react';
import { putJsonApi, type VoiceApps, type VoicePage, type VoiceToolChecks } from '../../api';
import { Button, LinkButton } from '../../components/Button';
import { Card } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { useAction } from '../../lib/useAction';
import { type ApiState, useApi } from '../../lib/useApi';

/** One app's check, as a badge: how many tools answered, or why none did. */
function CheckBadge({ checks, app }: { checks: ApiState<VoiceToolChecks>; app: VoiceApps['apps'][number] }) {
  if (!app.on) return null;
  // Its connector is not live: the app is stopped, so its assistant waits.
  if (app.ready === false) return <Badge>App stopped</Badge>;
  if (checks.loading && !checks.data) return <Badge>Checking…</Badge>;
  const found = checks.data?.apps.find((item) => item.id === app.id);
  // Checked with the viewer's own access: an app they cannot use is not theirs to check.
  if (!found) return checks.data ? <Badge>Not checked</Badge> : null;
  if (found.ok) return <Badge tone="green">{found.tools === 1 ? '1 tool' : `${found.tools} tools`}</Badge>;
  return (
    <Badge tone="amber">
      {found.tools === 0 && found.detail.startsWith('No tools') ? 'No tools' : 'Not answering'}
    </Badge>
  );
}

/** What the voice assistant can use: web search, and each app's assistant, which acts as the person talking. */
export function VoiceAppsCard({ page, readOnly = false }: { page: ApiState<VoicePage>; readOnly?: boolean }) {
  const { data, error, reload } = useApi<VoiceApps>('/api/v1/ai/voice/apps');
  const checks = useApi<VoiceToolChecks>(data?.installed ? '/api/v1/ai/voice/apps/check' : null);
  const { pending, run } = useAction();
  const title = 'Tools';
  const description =
    'What the voice assistant can use. An app’s assistant acts as the person talking, with their own access, and changes something only after they say yes.';
  if (!data || !data.installed)
    return (
      <Card title={title} description={description}>
        {error ? (
          <p className="error-text">Could not load: {error}</p>
        ) : data ? (
          <p className="muted">Install LiveKit from Apps to talk to the voice assistant.</p>
        ) : (
          <p className="muted card-loading">Loading…</p>
        )}
      </Card>
    );
  const change = async (id: string, on: boolean) => {
    if (await run(`app-${id}`, () => putJsonApi<VoiceApps>(`/api/v1/ai/voice/apps/${id}`, { on }))) {
      void reload();
      void checks.reload();
    }
  };
  const changeSearch = async (on: boolean) => {
    if (await run('search', () => putJsonApi<VoicePage>('/api/v1/ai/voice/settings', { search: on })))
      void page.reload();
  };
  const voice = page.data;
  // One line per problem, naming every app it affects.
  const problems = Object.entries(
    (checks.data?.apps ?? [])
      .filter((app) => !app.ok)
      .reduce<Record<string, string[]>>((found, app) => {
        const title = data?.apps.find((item) => item.id === app.id)?.title ?? app.id;
        return { ...found, [app.detail]: [...(found[app.detail] ?? []), title] };
      }, {}),
  );
  return (
    <Card
      title={title}
      description={description}
      actions={
        data.apps.length > 0 && (
          <Button
            size="sm"
            variant="ghost"
            icon={RefreshCw}
            loading={checks.loading}
            onClick={() => void checks.reload()}
          >
            Check again
          </Button>
        )
      }
    >
      <div className="form">
        {voice && (
          <label className="switch-field">
            <span className="switch-label">
              <span>
                Web search{' '}
                {!voice.search_installed && voice.settings.search && <Badge tone="amber">Needs SearXNG</Badge>}
              </span>
              <small>
                News, weather, opening hours and other current facts, through SearXNG on this server, with no account.
              </small>
              {!voice.search_installed && voice.search_app_id && (
                <span>
                  <LinkButton size="sm" to={`/apps/${voice.search_app_id}`}>
                    View SearXNG
                  </LinkButton>
                </span>
              )}
            </span>
            <input
              type="checkbox"
              role="switch"
              aria-label="Voice can search the web"
              checked={voice.settings.search}
              disabled={readOnly || pending === 'search'}
              onChange={(event) => void changeSearch(event.target.checked)}
            />
          </label>
        )}
        {data.apps.length === 0 ? (
          <p className="muted">
            No app assistants are set up yet. Turn on an app’s chat connector in Settings, Chat integrations.
          </p>
        ) : (
          data.apps.map((app) => (
            <label className="switch-field" key={app.id}>
              <span className="switch-label">
                <span>
                  {app.title} {app.operators_only && <Badge>Administrators</Badge>}{' '}
                  <CheckBadge checks={checks} app={app} />
                </span>
                <small>{app.description}</small>
              </span>
              <input
                type="checkbox"
                role="switch"
                aria-label={`Voice can use ${app.title}`}
                checked={app.on}
                disabled={readOnly || pending === `app-${app.id}`}
                onChange={(event) => void change(app.id, event.target.checked)}
              />
            </label>
          ))
        )}
        {problems.map(([detail, titles]) => (
          <p className="error-text" key={detail}>
            {titles.join(', ')}: {detail}
          </p>
        ))}
        {checks.error && <p className="error-text">Could not check the apps: {checks.error}</p>}
        {checks.data && <small className="muted">Checked with your own access, as a call of yours would use it.</small>}
      </div>
    </Card>
  );
}
