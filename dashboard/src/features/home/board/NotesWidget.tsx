import { ExternalLink, FilePlus2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { type HomeWidgetActionResult, type HomeWidgetData, type HomeWidgetKind, postJsonApi } from '../../../api';
import { Button } from '../../../components/Button';
import { errorText } from '../../../lib/format';
import { useApi } from '../../../lib/useApi';
import { dataPath, refreshWidget, SavedBanner, Unavailable } from './AppWidget';
import { useBox } from './size';
import type { SettingsProps, WidgetProps } from './widgets';

interface NoteDocument {
  id: string;
  title: string;
  text: string;
  url: string;
  updated_at: string;
}

interface NotesView {
  document?: NoteDocument | null;
  documents?: { id: string; title: string }[];
  missing?: boolean;
}

// Saved this long after the last key press, and at once when the note loses focus.
const PAUSE = 1200;

const settingsOf = (widget: WidgetProps['widget']) => widget.settings as { document_id?: string };
const pathOf = (widget: WidgetProps['widget']) =>
  `${dataPath(widget.kind)}?settings=${encodeURIComponent(JSON.stringify({ document_id: settingsOf(widget).document_id ?? '' }))}`;
const act = (widget: WidgetProps['widget'], action: string, data: object = {}) =>
  postJsonApi<HomeWidgetActionResult>(`/api/v1/home/widgets/${encodeURIComponent(widget.kind)}/action`, {
    action,
    data,
    settings: { document_id: settingsOf(widget).document_id ?? '' },
  });

type Saving = 'saved' | 'waiting' | 'saving' | 'failed';

/**
 * One Outline document, written right here: typing saves to Outline as the person after a short
 * pause. While Outline is stopped the last saved text shows, read-only.
 */
export function NotesWidget({ widget, kind, onConfigure }: WidgetProps & { kind?: HomeWidgetKind }) {
  const { height } = useBox();
  const tiny = height > 0 && height < 110;
  const { data, error, reload } = useApi<HomeWidgetData>(pathOf(widget), {
    interval: (kind?.refresh_seconds ?? 300) * 1000,
  });
  const view = (data?.view ?? {}) as NotesView;
  const document = view.document ?? null;
  const [draft, setDraft] = useState<string | null>(null);
  const [state, setState] = useState<Saving>('saved');
  // When the text in the box was last saved or loaded, so Outline can tell if someone else changed it since.
  const base = useRef('');
  const timer = useRef(0);
  // Typed here and not saved yet (null: nothing waiting), and what it belongs to, for saving on the way out.
  const latest = useRef<string | null>(null);
  const pending = useRef<{ id: string } | null>(null);
  const documentId = document?.id ?? '';
  const loadedAt = document?.updated_at ?? '';
  useEffect(() => {
    // A document opened (or another one chosen): that is the version typing starts from.
    if (latest.current === null) base.current = loadedAt;
  }, [documentId, loadedAt]);
  // A newer version from Outline replaces the box only while nothing typed here is waiting to be saved.
  const shown = draft ?? document?.text ?? '';

  const save = async () => {
    window.clearTimeout(timer.current);
    const text = latest.current;
    if (text === null || !document) return;
    setState('saving');
    try {
      const reply = await act(widget, 'save', { id: document.id, text, updated_at: base.current });
      base.current = String(reply.result?.updated_at ?? '') || base.current;
      if (latest.current === text) {
        latest.current = null;
        pending.current = null;
        setDraft(null);
        setState('saved');
      } else setState('waiting');
    } catch (cause) {
      setState('failed');
      toast.error(`Your note was not saved: ${errorText(cause)}`);
    }
  };
  // Leaving the page (or the widget going) with typing not saved yet: it is saved on the way out.
  useEffect(
    () => () => {
      window.clearTimeout(timer.current);
      const text = latest.current;
      const target = pending.current;
      if (text !== null && target)
        void act(widget, 'save', { id: target.id, text, updated_at: base.current }).catch(() => undefined);
    },
    // Only on the way out: what to save is read from the refs then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  if (!data) return error ? <p className="appw-state appw-error">{error}</p> : <p className="muted">Loading…</p>;
  const saved = data.state !== 'ok' && Boolean(data.saved);
  const retry = () =>
    void refreshWidget(widget.kind)
      .catch(() => undefined)
      .then(() => reload());
  if (data.state !== 'ok' && !saved)
    return <Unavailable data={data} onRetry={retry} onConfigure={onConfigure} takesKey={false} tiny={tiny} />;
  if (!document)
    return (
      <div className="widget-empty">
        <p>
          {view.missing
            ? 'That document is gone, or you can no longer open it.'
            : 'Choose an Outline document to write in here.'}
        </p>
        <Button variant="secondary" size="sm" onClick={onConfigure}>
          Choose a document
        </Button>
      </div>
    );
  const readOnly = saved;
  return (
    <div className={`notes${saved ? ' is-saved' : ''}`}>
      {saved && <SavedBanner data={data} onRetry={retry} tiny={tiny} />}
      <textarea
        className="notes-text"
        aria-label={document.title}
        value={shown}
        readOnly={readOnly}
        placeholder={readOnly ? '' : 'Write here. It saves to Outline as you type.'}
        onChange={(event) => {
          const text = event.target.value;
          setDraft(text);
          latest.current = text;
          pending.current = { id: document.id };
          setState('waiting');
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => void save(), PAUSE);
        }}
        onBlur={() => {
          if (latest.current !== null) void save();
        }}
      />
      {!tiny && (
        <p className="notes-foot muted">
          <span className={`notes-state is-${state}`} role="status">
            {readOnly
              ? 'Read-only until Outline answers again'
              : state === 'saving'
                ? 'Saving…'
                : state === 'waiting'
                  ? 'Typing…'
                  : state === 'failed'
                    ? 'Not saved'
                    : 'Saved'}
          </span>
          {document.url && (
            <a href={document.url} target="_blank" rel="noreferrer">
              {document.title} <ExternalLink aria-hidden />
            </a>
          )}
        </p>
      )}
    </div>
  );
}

export function NotesSettings({ widget, onChange }: SettingsProps) {
  const { data } = useApi<HomeWidgetData>(pathOf(widget));
  const view = (data?.view ?? {}) as NotesView;
  const documents = view.documents ?? [];
  const chosen = settingsOf(widget).document_id ?? '';
  const [making, setMaking] = useState(false);
  const make = async () => {
    setMaking(true);
    try {
      const reply = await act({ ...widget, settings: {} }, 'create');
      const id = String(reply.result?.document_id ?? '');
      if (id) onChange({ ...widget.settings, document_id: id });
    } catch (cause) {
      toast.error(`No document was made: ${errorText(cause)}`);
    } finally {
      setMaking(false);
    }
  };
  return (
    <div className="widget-setting">
      <span className="widget-setting-label">Document</span>
      {data && data.state !== 'ok' ? (
        <p className="muted widget-setting-hint">{data.detail || 'Outline is not answering right now.'}</p>
      ) : (
        <select
          className="widget-input"
          aria-label="Document"
          value={chosen}
          onChange={(event) => onChange({ ...widget.settings, document_id: event.target.value })}
        >
          <option value="">Choose one…</option>
          {chosen && !documents.some((item) => item.id === chosen) && (
            <option value={chosen}>{view.document?.title ?? 'The chosen document'}</option>
          )}
          {documents.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
      )}
      <Button variant="ghost" size="sm" icon={FilePlus2} loading={making} onClick={() => void make()}>
        Make a new “Home notes” document
      </Button>
      <p className="muted widget-setting-hint">
        Your 25 most recently changed documents. It saves as you, so Outline keeps its history.
      </p>
    </div>
  );
}
