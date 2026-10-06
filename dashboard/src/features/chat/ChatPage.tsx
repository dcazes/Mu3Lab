import { MessageSquare, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { postApi } from '../../api/client';
import { ExternalButton, LinkButton } from '../../components/Button';
import { EmptyState } from '../../components/Layout';
import type { ChatAssistant } from '../../api/models';
import { useDashboard } from '../../state/dashboard';

function expectedMcp(): { name: string; serviceId: string } | null {
  try {
    const value = JSON.parse(window.sessionStorage.getItem('mu3lab.expectedMcp') || 'null');
    return value && typeof value.name === 'string' && typeof value.serviceId === 'string' ? value : null;
  } catch {
    return null;
  }
}

function AssistantList({ assistants }: { assistants: ChatAssistant[] }) {
  const ready = assistants.filter((item) => item.status === 'ready');
  if (!assistants.length || ready.length === assistants.length) return null;
  return (
    <details className="chat-banner chat-assistants">
      <summary>
        Assistants in chat: {ready.length} of {assistants.length} apps
      </summary>
      <ul>
        {assistants.map((item) => (
          <li key={item.id} data-status={item.status}>
            <b>{item.name}</b> — {item.detail}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function ChatPage() {
  const { data } = useDashboard();
  const status = data.chat;
  const [expected, setExpected] = useState(expectedMcp);
  const [connection, setConnection] = useState<{ url: string; interval: number } | null>(null);
  const [connected, setConnected] = useState(false);
  const [message, setMessage] = useState('');
  const started = useRef(false);
  const needsConnection = status.ready && !!status.url && !(status.connected || connected);
  useEffect(() => {
    if (!connection) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await postApi<{ state: string; interval?: number; detail?: string }>(
          '/api/v1/chat/connect/poll',
        );
        if (stopped) return;
        if (result.state === 'connected') {
          setConnected(true);
          setConnection(null);
          setMessage(result.detail || 'Your assistants are ready.');
        } else timer = setTimeout(poll, (result.interval || connection!.interval) * 1000);
      } catch (error) {
        if (!stopped) {
          setMessage(error instanceof Error ? error.message : 'Chat could not connect. Reload to try again.');
          setConnection(null);
        }
      }
    }
    timer = setTimeout(poll, connection.interval * 1000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [connection]);
  // Start the one-time approval by itself; the person only confirms it inside the chat below.
  useEffect(() => {
    if (!needsConnection || started.current) return;
    started.current = true;
    postApi<{ verification_uri_complete: string; interval: number }>('/api/v1/chat/connect')
      .then((result) => setConnection({ url: result.verification_uri_complete, interval: result.interval }))
      .catch((error) =>
        setMessage(error instanceof Error ? error.message : 'Chat could not connect. Reload to try again.'),
      );
  }, [needsConnection]);
  // The approval page needs a chat session, so it goes through the same silent sign-in as the chat itself.
  const approval = connection ? new URL(connection.url) : null;
  const frameUrl = approval
    ? `${status.url}?next=${encodeURIComponent(approval.pathname + approval.search)}`
    : status.url;
  if (!status.ready || !status.url)
    return (
      <div className="page">
        <EmptyState
          icon={MessageSquare}
          title="Chat isn’t ready yet"
          action={<LinkButton to="/apps/lobehub">Check LobeChat</LinkButton>}
        >
          {status.detail || 'LobeChat is starting or still being set up.'}
        </EmptyState>
      </div>
    );
  return (
    <div className="chat">
      {connection && (
        <div className="chat-banner" role="status">
          One-time step: choose <b>Authorize</b> below so Mu3Lab can add your app assistants to chat.
        </div>
      )}
      {message && (
        <div className="chat-banner" role="status">
          {message}
        </div>
      )}
      <AssistantList assistants={status.assistants || []} />
      {expected && (
        <div className="chat-banner" role="status">
          <span>
            {/* Say "available" only when the assistant really is; otherwise say why not. */}
            <b>{expected.name}</b>{' '}
            {(() => {
              const item = status.assistants?.find((entry) => entry.id === expected.serviceId);
              return !item || item.status === 'ready' ? 'is now available in chat.' : `: ${item.detail}`;
            })()}
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-sm btn-icon"
            aria-label="Dismiss"
            onClick={() => {
              window.sessionStorage.removeItem('mu3lab.expectedMcp');
              setExpected(null);
            }}
          >
            <X />
          </button>
        </div>
      )}
      <iframe title="Mu3Lab chat" src={frameUrl} allow="clipboard-read; clipboard-write; microphone" />
      <ExternalButton size="sm" className="chat-popout" href={status.url}>
        Open in new window
      </ExternalButton>
    </div>
  );
}
