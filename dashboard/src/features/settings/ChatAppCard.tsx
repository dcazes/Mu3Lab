import { MessageSquare } from 'lucide-react';
import { type ChatApps, putJsonApi } from '../../api';
import { LinkButton } from '../../components/Button';
import { Card } from '../../components/Layout';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

/** Which installed chat app the Chat page shows for the whole household. */
export function ChatAppCard({ readOnly }: { readOnly: boolean }) {
  const { data, error, reload } = useApi<ChatApps>('/api/v1/chat/apps');
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const title = (
    <>
      <MessageSquare className="inline-icon" /> Chat app
    </>
  );
  const description = 'The chat app the household sees on the Chat page. Each one keeps its own conversations.';
  if (!data)
    return (
      <Card title={title} description={description}>
        {error ? (
          <p className="error-text card-loading">Could not load: {error}</p>
        ) : (
          <p className="muted card-loading">Loading…</p>
        )}
      </Card>
    );
  // An older control plane, mid-update, may not answer this yet.
  const apps = data.apps ?? [];
  if (!apps.length) return null;
  const choose = async (id: string, name: string) => {
    const saved = await run(
      'chat',
      () => putJsonApi<ChatApps>('/api/v1/chat/apps', { app_id: id }),
      `Chat now shows ${name}`,
    );
    if (saved) {
      void reload();
      void refresh();
    }
  };
  const missing = apps.filter((app) => !app.installed);
  const installed = apps.filter((app) => app.installed);
  const others = !readOnly && missing.length > 0 && (
    <LinkButton size="sm" variant="ghost" to={`/apps/${missing[0].id}`}>
      Get {missing[0].name}
    </LinkButton>
  );
  // With one chat app there is nothing to choose: say which it is, and how to get another.
  if (installed.length === 1)
    return (
      <Card title={title} description={description} actions={others || undefined}>
        <p>
          Chat uses <b>{installed[0].name}</b>.
          {others && ` Install ${missing.map((app) => app.name).join(' or ')} to switch.`}
        </p>
      </Card>
    );
  return (
    <Card title={title} description={description}>
      <div className="segmented" role="radiogroup" aria-label="Chat app">
        {apps
          .filter((app) => app.installed)
          .map((app) => (
            <button
              key={app.id}
              type="button"
              role="radio"
              aria-checked={app.selected}
              disabled={readOnly || Boolean(pending)}
              onClick={() => !app.selected && void choose(app.id, app.name)}
            >
              {app.name}
            </button>
          ))}
      </div>
      {others && (
        <p className="muted">
          {missing.map((app) => app.name).join(' and ')} can be installed from Discover to choose it here. {others}
        </p>
      )}
    </Card>
  );
}
