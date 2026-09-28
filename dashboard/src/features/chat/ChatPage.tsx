import { MessageSquare, X } from 'lucide-react';
import { useState } from 'react';
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
