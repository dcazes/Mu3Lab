import { ArrowRight, MessageCircle, Send } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { toast } from 'sonner';
import { type AskResponse, postJsonApi } from '../../../api';
import { Button } from '../../../components/Button';
import { errorText } from '../../../lib/format';
import { CHAT_QUESTION } from '../../../lib/chatQuestion';
import { navigate } from '../../../lib/router';
import { useDashboard } from '../../../state/dashboard';
import { useBox } from './size';
import type { WidgetProps } from './widgets';

/**
 * A quick question to the household's assistant, answered right here. Anything that needs the
 * person's own data in an app goes on in Chat, where the app assistants are.
 */
export function AskWidget(_props: WidgetProps) {
  const { data: dashboard } = useDashboard();
  const chat = dashboard.chat;
  const { width, height } = useBox();
  const measured = width > 0;
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

  // The chat app opens on the Chat page with the question already typed in, where the app
  // assistants can take it further. A chat app that cannot be handed a question gets it on the
  // clipboard instead.
  const continueInChat = async () => {
    const text = asked || question.trim();
    if (text && chat.prompt_url) {
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
      {!pending && !answer && !error && (!measured || height >= 140) && (
        <p className="ask-hint">
          Quick questions are answered here. Anything about your own data in an app goes on in {chat.app_name || 'Chat'}
          .
        </p>
      )}
      {(answer || error) && (
        <button type="button" className="widget-link ask-continue" onClick={() => void continueInChat()}>
          Continue in {chat.app_name || 'Chat'}
          <ArrowRight />
        </button>
      )}
    </div>
  );
}
