import { Flame, Search, Send } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { postJsonApi, type SearchModes, type SearchResponse } from '../../../api';
import { Button } from '../../../components/Button';
import { errorText } from '../../../lib/format';
import { useApi } from '../../../lib/useApi';
import { useBox } from './size';
import type { WidgetProps } from './widgets';

// web: the web search app (SearXNG). fetch: the page reader (Firecrawl).
type Mode = 'web' | 'fetch';

const remembered = (id: string): Mode => {
  try {
    return window.localStorage.getItem(`mu3lab.search.${id}`) === 'fetch' ? 'fetch' : 'web';
  } catch {
    return 'web';
  }
};

const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
};

/**
 * Search the web privately (SearXNG), or switch to Firecrawl: paste an address to read the page
 * here, or type words for a deep search that reads the top pages. Results scroll, like Ask's answer.
 */
export function SearchWidget({ widget }: WidgetProps) {
  const { width, height } = useBox();
  const measured = width > 0;
  const { data: modes } = useApi<SearchModes>('/api/v1/home/search');
  const [chosen, setChosen] = useState<Mode>(() => remembered(widget.id));
  // Firecrawl only while it is installed; the switch shows only when both halves work.
  const mode: Mode = modes?.fetch && (chosen === 'fetch' || !modes.web) ? 'fetch' : 'web';
  const both = Boolean(modes?.web && modes?.fetch);
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<SearchResponse | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);

  const choose = (next: Mode) => {
    setChosen(next);
    setFound(null);
    setError('');
    try {
      window.localStorage.setItem(`mu3lab.search.${widget.id}`, next);
    } catch {
      /* Private browsing: the switch still works until the page reloads. */
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = query.trim();
    if (!text || pending) return;
    setPending(true);
    setError('');
    try {
      setFound(await postJsonApi<SearchResponse>('/api/v1/home/search', { query: text, mode }));
    } catch (cause) {
      setFound(null);
      setError(errorText(cause));
    } finally {
      setPending(false);
    }
  };

  const Icon = mode === 'fetch' ? Flame : Search;
  const page = found?.page;
  const results = found?.results ?? [];
  return (
    <div className="search">
      <form className="ask-form" onSubmit={(event) => void submit(event)}>
        {(!measured || width >= 260) && <Icon aria-hidden className="ask-icon" />}
        <input
          className="widget-input"
          aria-label={mode === 'fetch' ? 'A web address to read, or words for a deep search' : 'Search the web'}
          placeholder={mode === 'fetch' ? 'Paste an address, or search deeper…' : 'Search the web…'}
          value={query}
          maxLength={500}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Button type="submit" variant="primary" size="sm" icon={Send} loading={pending} aria-label="Search" />
      </form>
      {both && (
        <div className="segmented search-modes" role="radiogroup" aria-label="Search with">
          {(['web', 'fetch'] as const).map((value) => (
            <button key={value} type="button" role="radio" aria-checked={mode === value} onClick={() => choose(value)}>
              {value === 'web' ? 'Web' : modes?.fetch_name || 'Read pages'}
            </button>
          ))}
        </div>
      )}
      {(pending || error || found) && (
        <div className="search-found widget-scroll" aria-live="polite">
          {pending ? (
            <p className="muted">
              {mode === 'fetch'
                ? `${modes?.fetch_name || 'The page reader'} is reading… this can take a little while.`
                : 'Searching…'}
            </p>
          ) : error ? (
            <p className="widget-error">{error}</p>
          ) : page ? (
            <article className="search-page">
              <a href={page.url} target="_blank" rel="noreferrer">
                <strong>{page.title}</strong>
                <small>{host(page.url)}</small>
              </a>
              <p>{page.text}</p>
            </article>
          ) : (
            <>
              {found?.answer && <p className="search-answer">{found.answer}</p>}
              {results.length ? (
                <ul className="search-results">
                  {results.map((result) => (
                    <li key={result.url}>
                      <a href={result.url} target="_blank" rel="noreferrer">
                        <strong>{result.title}</strong>
                        <small>
                          {host(result.url)}
                          {result.read && ` · read by ${modes?.fetch_name || 'the page reader'}`}
                        </small>
                      </a>
                      {result.snippet && <p>{result.snippet}</p>}
                    </li>
                  ))}
                </ul>
              ) : (
                !found?.answer && <p className="muted">Nothing was found.</p>
              )}
            </>
          )}
        </div>
      )}
      {!pending && !error && !found && (!measured || height >= 140) && (
        <p className="ask-hint">
          {modes && !modes.web && !modes.fetch
            ? `Install ${modes.web_name || 'the web search app'} from Apps to search the web from here.`
            : mode === 'fetch'
              ? 'Paste a web address to read the page here, or type words for a deep search that reads the top pages.'
              : 'A private web search: no account, and nothing kept about what you search.'}
        </p>
      )}
    </div>
  );
}
