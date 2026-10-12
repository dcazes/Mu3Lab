import { MessageSquare } from 'lucide-react';
import { type ChatApps, putJsonApi } from '../../api';
import { LinkButton } from '../../components/Button';
import { Card } from '../../components/Layout';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

/** Which installed chat app the Chat page shows for the whole household. */
export function ChatAppCard({ readOnly }: { readOnly: boolean }) {
  const { data, reload } = useApi<ChatApps>('/api/v1/chat/apps');
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  // An older control plane, mid-update, may not answer this yet.
  const apps = data?.apps ?? [];
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
  return (
    <Card
      title={
        <>
          <MessageSquare className="inline-icon" /> Chat app
        </>
      }
      description="The chat the household sees on the Chat page. Each one keeps its own conversations; the other stays installed until you remove it."
    >
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
      {!readOnly && missing.length > 0 && (
        <p className="muted">
          {missing.map((app) => app.name).join(' and ')} can be installed from Discover to choose it here.{' '}
          <LinkButton size="sm" variant="ghost" to={`/apps/${missing[0].id}`}>
            View {missing[0].name}
          </LinkButton>
        </p>
      )}
    </Card>
  );
}
