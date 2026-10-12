import { MessageSquare } from 'lucide-react';
import { toast } from 'sonner';
import { useEffect, useRef, useState } from 'react';
import { postApi } from '../../api/client';
import { ExternalButton, LinkButton } from '../../components/Button';
import { EmptyState } from '../../components/Layout';
import type { AiModels } from '../../api/models';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

function expectedMcp(): { name: string; serviceId: string } | null {
  try {
    const value = JSON.parse(window.sessionStorage.getItem('mu3lab.expectedMcp') || 'null');
    return value && typeof value.name === 'string' && typeof value.serviceId === 'string' ? value : null;
  } catch {
    return null;
  }
}

/** Where messages go, said as each visit opens (R31); a toast so it never shrinks the chat. */
function useWhereChatRuns() {
  const { data } = useApi<AiModels>('/api/v1/ai/models');
  const said = useRef(false);
  useEffect(() => {
    if (!data || said.current) return;
    said.current = true;
    const local = data.chat_models.find((model) => model.id === data.settings.chat_model);
    toast.info(
      data.settings.privacy === 'local_only'
        ? `Local only: every chat runs on this server${local ? ` with ${local.name}` : ''}.`
        : local
          ? `“Mu3Lab chat” sends messages to outside AI providers. Choose “${local.name} (private, on this server)” to keep a chat here.`
          : '“Mu3Lab chat” sends messages, and anything an assistant includes, to outside AI providers.',
      { id: 'chat-privacy' },
    );
  }, [data]);
}

export function ChatPage() {
  const { data } = useDashboard();
  const status = data.chat;
  const [expected] = useState(expectedMcp);
  const [connection, setConnection] = useState<{ url: string; interval: number } | null>(null);
  const [connected, setConnected] = useState(false);
  const started = useRef(false);
  useWhereChatRuns();
  // An app that was just connected says so once, then the note is spent.
  const announced = useRef(false);
  useEffect(() => {
    if (!expected || announced.current) return;
    announced.current = true;
    const item = status.assistants?.find((entry) => entry.id === expected.serviceId);
    if (!item || item.status === 'ready') toast.success(`${expected.name} is now available in chat.`);
    else toast.info(`${expected.name}: ${item.detail}`);
    window.sessionStorage.removeItem('mu3lab.expectedMcp');
  }, [expected, status.assistants]);
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
          toast.success(result.detail || 'Your assistants are ready.');
        } else timer = setTimeout(poll, (result.interval || connection!.interval) * 1000);
      } catch (error) {
        if (!stopped) {
          toast.error(error instanceof Error ? error.message : 'Chat could not connect. Reload to try again.');
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
        toast.error(error instanceof Error ? error.message : 'Chat could not connect. Reload to try again.'),
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
      {/* The one notice that waits for a click; everything else is a passing toast. */}
      {connection && (
        <div className="chat-banner" role="status">
          One-time step: choose <b>Authorize</b> below so Mu3Lab can add your app assistants to chat.
        </div>
      )}
      <iframe title="Mu3Lab chat" src={frameUrl} allow="clipboard-read; clipboard-write; microphone" />
      <ExternalButton size="sm" className="chat-popout" href={status.url}>
        Open in new window
      </ExternalButton>
    </div>
  );
}
