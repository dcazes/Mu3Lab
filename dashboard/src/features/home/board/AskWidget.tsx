import { ArrowRight, MessageCircle, Send } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { toast } from 'sonner';
import { type AskChats, type AskResponse, postJsonApi } from '../../../api';
import { Button } from '../../../components/Button';
import { errorText, relativeTime } from '../../../lib/format';
import { CHAT_QUESTION } from '../../../lib/chatQuestion';
import { navigate } from '../../../lib/router';
import { useApi } from '../../../lib/useApi';
import { FitList, useBox } from './size';
import type { WidgetProps } from './widgets';

const storeKey = (id: string) => `mu3lab.ask.${id}`;

const remembered = (id: string) => {
  try {
    return window.localStorage.getItem(storeKey(id)) ?? '';
  } catch {
    return '';
  }
};

/** Opens a chat app with the question typed in (see ``prompt_url``). */
const withQuestion = (url: string, question: string) =>
  url
    .replace('{question2}', encodeURIComponent(encodeURIComponent(question)))
    .replace('{question}', encodeURIComponent(question));

/**
 * A quick question to the household's assistant, answered right here. A switch picks which chat app
 * the conversation goes on in (Open WebUI, LobeChat or LibreChat, whichever are installed), and while
 * nothing is asked the widget lists the person's own newest chats there.
 */
export function AskWidget({ widget }: WidgetProps) {
  const { width, height } = useBox();
  const measured = width > 0;
  const [chosen, setChosen] = useState(() => remembered(widget.id));
  const { data: chats } = useApi<AskChats>(`/api/v1/home/ask/chats?app=${encodeURIComponent(chosen)}`, {
    interval: 60_000,
  });
  const appName = chats?.app_name || 'Chat';
  const choose = (id: string) => {
    setChosen(id);
    try {
      window.localStorage.setItem(storeKey(widget.id), id);
    } catch {
      /* Private browsing: the switch still works until the page reloads. */
    }
  };
  const [question, setQuestion] = useState('');
  const [asked, setAsked] = useState('');
  const [answer, setAnswer] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || pending) return;
    setPending(true);
    setAsked(text);
    setAnswer('');
    setError('');
    try {
      const result = await postJsonApi<AskResponse>('/api/v1/home/ask', { question: text });
      setAnswer(result.answer);
      setQuestion('');
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPending(false);
    }
  };

  // The household's chat app opens on the Chat page with the question already typed in, where the
  // app assistants can take it further; another chat app opens in a new tab with it typed in. A
  // chat app that cannot be handed a question gets it on the clipboard instead.
  const continueInChat = async () => {
    const text = asked || question.trim();
    if (chats?.app_id && chats.app_id !== chats.household_id) {
      if (chats.prompt_url && text) {
        window.open(withQuestion(chats.prompt_url, text), '_blank', 'noopener');
        return;
      }
      if (text) {
        try {
          await navigator.clipboard.writeText(text);
          toast.success(`Your question is copied: paste it into ${appName}.`);
        } catch {
          /* The app still opens; the question is on screen to retype. */
        }
      }
      const home = chats.prompt_url ? new URL(chats.prompt_url).origin : '';
      if (home) window.open(home, '_blank', 'noopener');
      return;
    }
    if (text && chats?.prompt_url) {
      try {
        window.sessionStorage.setItem(CHAT_QUESTION, text);
      } catch {
        /* Private browsing: chat still opens, without the question. */
      }
    } else if (text) {
      try {
        await navigator.clipboard.writeText(text);
        toast.success('Your question is copied: paste it into chat.');
      } catch {
        /* Chat still opens; the question is on screen to retype. */
      }
    }
    navigate('/chat');
  };

  return (
    <div className="ask">
      <form className="ask-form" onSubmit={(event) => void submit(event)}>
        {(!measured || width >= 260) && <MessageCircle aria-hidden className="ask-icon" />}
        <input
          className="widget-input"
          aria-label="Ask the Mu3Lab assistant"
          placeholder="Ask anything…"
          value={question}
          maxLength={2000}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <Button type="submit" variant="primary" size="sm" icon={Send} loading={pending} aria-label="Ask" />
      </form>
      {(chats?.apps.length ?? 0) > 1 && (!measured || height >= 140) && (
        <AppSwitch chats={chats!} wide={!measured || width >= 380} onChoose={choose} />
      )}
      {(pending || answer || error) && (
        <div className="ask-answer widget-scroll" aria-live="polite">
          <p className="ask-question">{asked}</p>
          {pending ? (
            <p className="muted">Thinking…</p>
          ) : error ? (
            <p className="widget-error">{error}</p>
          ) : (
            <p className="ask-text">{answer}</p>
          )}
        </div>
      )}
      {!pending && !answer && !error && (!measured || height >= 140) && <RecentChats chats={chats} />}
      {(answer || error) && (
        <button type="button" className="widget-link ask-continue" onClick={() => void continueInChat()}>
          Continue in {appName}
          <ArrowRight />
        </button>
      )}
    </div>
  );
}

/** Which chat app the widget goes on in: buttons when there is room, a menu when narrow. */
function AppSwitch({ chats, wide, onChoose }: { chats: AskChats; wide: boolean; onChoose: (id: string) => void }) {
  if (!wide)
    return (
      <select
        className="ask-apps-select"
        aria-label="Chat app"
        value={chats.app_id}
        onChange={(event) => onChoose(event.target.value)}
      >
        {chats.apps.map((app) => (
          <option key={app.id} value={app.id}>
            {app.name}
          </option>
        ))}
      </select>
    );
  return (
    <div className="segmented ask-apps" role="radiogroup" aria-label="Chat app">
      {chats.apps.map((app) => (
        <button
          key={app.id}
          type="button"
          role="radio"
          aria-checked={chats.app_id === app.id}
          onClick={() => onChoose(app.id)}
        >
          {app.name}
        </button>
      ))}
    </div>
  );
}

/** The person's own newest chats in the chosen app, each opening there; titles and times only. */
function RecentChats({ chats }: { chats: AskChats | null }) {
  if (!chats) return <div className="ask-recent" />;
  const list = chats.chats ?? [];
  if (!list.length)
    return (
      <p className="ask-hint">
        {chats.detail ||
          `Quick questions are answered here. Your chats in ${chats.app_name || 'Chat'} will be listed here too.`}
      </p>
    );
  return (
    <div className="ask-recent">
      <FitList className="ask-recent-list" label={`Your chats in ${chats.app_name}`}>
        {list.map((item) => (
          <li key={item.url}>
            <a href={item.url} target="_blank" rel="noreferrer">
              <span>{item.title}</span>
              {(item.updated_at ?? 0) > 0 && (
                <small>{relativeTime(new Date((item.updated_at ?? 0) * 1000).toISOString())}</small>
              )}
            </a>
          </li>
        ))}
      </FitList>
    </div>
  );
}
