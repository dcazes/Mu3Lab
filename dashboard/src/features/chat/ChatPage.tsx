import { MessageSquare, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { postApi } from '../../api/client';
import { ExternalButton, LinkButton } from '../../components/Button';
import { EmptyState } from '../../components/Layout';
import { useDashboard } from '../../state/dashboard';

function expectedMcp(): { name: string; serviceId: string } | null {
  try {
    const value = JSON.parse(window.sessionStorage.getItem('mu3lab.expectedMcp') || 'null');
    return value && typeof value.name === 'string' && typeof value.serviceId === 'string' ? value : null;
  } catch {
    return null;
  }
}

export function ChatPage() {
  const { data } = useDashboard();
  const status = data.chat;
  const [expected, setExpected] = useState(expectedMcp);
  const [connection, setConnection] = useState<{ url: string; code: string; interval: number } | null>(null);
  const [connected, setConnected] = useState(false);
  const [message, setMessage] = useState('');
  const [connecting, setConnecting] = useState(false);
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
          setMessage(error instanceof Error ? error.message : 'Chat could not connect. Try again.');
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
  async function connect() {
    setConnecting(true);
    setMessage('');
    // Open during the click so browsers permit the approval tab.
    const approval = window.open('about:blank', '_blank');
    try {
      const result = await postApi<{ verification_uri_complete: string; user_code: string; interval: number }>(
        '/api/v1/chat/connect',
      );
      if (approval) {
        approval.opener = null;
        approval.location.href = result.verification_uri_complete;
      }
      setConnection({ url: result.verification_uri_complete, code: result.user_code, interval: result.interval });
    } catch (error) {
      approval?.close();
      setMessage(error instanceof Error ? error.message : 'Chat could not connect. Try again.');
    } finally {
      setConnecting(false);
    }
  }
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
      {!(status.connected || connected) && (
        <div className="chat-banner" role="status">
          {connection ? (
            <span>
              Approve code <b>{connection.code}</b> in chat.{' '}
              <a href={connection.url} target="_blank" rel="noreferrer">
                Open approval
              </a>
            </span>
          ) : (
            <button className="btn btn-primary" disabled={connecting} onClick={connect}>
              Connect chat (one time)
            </button>
          )}
        </div>
      )}
      {message && (
        <div className="chat-banner" role="status">
          {message}
        </div>
      )}
      {expected && (
        <div className="chat-banner" role="status">
          <span>
            <b>{expected.name}</b> is now available in chat.
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
      <iframe title="Mu3Lab chat" src={status.url} allow="clipboard-read; clipboard-write; microphone" />
      <ExternalButton size="sm" className="chat-popout" href={status.url}>
        Open in new window
      </ExternalButton>
    </div>
  );
}
