import { type VoiceApps, putJsonApi } from '../../api';
import { Card } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';

/** Which app assistants the voice assistant may hand people to (ctl/voice_tools.py). */
export function VoiceAppsCard({ readOnly = false }: { readOnly?: boolean }) {
  const { data, error, reload } = useApi<VoiceApps>('/api/v1/ai/voice/apps');
  const { pending, run } = useAction();
  const title = 'Apps the voice assistant can use';
  const description =
    'The voice assistant hands you to an app’s assistant, which acts as you with your own access, like chat does. It changes something only after you say yes.';
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
    if (await run(`app-${id}`, () => putJsonApi<VoiceApps>(`/api/v1/ai/voice/apps/${id}`, { on }))) void reload();
  };
  return (
    <Card title={title} description={description}>
      {data.apps.length === 0 ? (
        <p className="muted">
          No app assistants are set up yet. Turn on an app’s chat connector in Settings, Chat integrations.
        </p>
      ) : (
        data.apps.map((app) => (
          <label className="switch-field" key={app.id}>
            <span>
              {app.title}
              {app.operators_only && <Badge>Administrators</Badge>}
              <small className="muted"> {app.description}</small>
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
    </Card>
  );
}
