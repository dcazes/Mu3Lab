import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';

/** Runs public/sw.js against a fake service worker scope and cache storage. */
function load(release: string, existing: string[]) {
  const listeners: Record<string, (event: unknown) => void> = {};
  const stores = new Map<string, Map<string, Response>>(existing.map((name) => [name, new Map()]));
  const caches = {
    keys: async () => [...stores.keys()],
    delete: async (name: string) => stores.delete(name),
    open: async (name: string) => {
      if (!stores.has(name)) stores.set(name, new Map());
      const store = stores.get(name)!;
      return {
        addAll: async () => undefined,
        put: async (request: Request | string, response: Response) =>
          void store.set(typeof request === 'string' ? request : new URL(request.url).pathname, response),
      };
    },
    match: async () => undefined,
  };
  const scope = {
    location: new URL(`https://host.ts.net/sw.js?release=${release}`),
    addEventListener: (type: string, listener: (event: unknown) => void) => (listeners[type] = listener),
    skipWaiting: async () => undefined,
    clients: { claim: async () => undefined },
  };
  // Response.type is 'default' in Node; a same-origin browser response is 'basic'.
  const fetchMock = vi.fn(async () => Object.defineProperty(new Response('asset'), 'type', { value: 'basic' }));
  new Function('self', 'caches', 'fetch', readFileSync('public/sw.js', 'utf8'))(scope, caches, fetchMock);
  return { listeners, stores };
}

describe('service worker (review R24)', () => {
  it("each release has its own cache and activating it deletes the last release's", async () => {
    const { listeners, stores } = load('index-NEW.js', ['mu3lab-shell-v1', 'mu3lab-shell-index-OLD.js']);
    const pending: Promise<unknown>[] = [];
    listeners.install({ waitUntil: (work: Promise<unknown>) => pending.push(work) });
    listeners.activate({ waitUntil: (work: Promise<unknown>) => pending.push(work) });
    await Promise.all(pending);
    expect([...stores.keys()]).toEqual(['mu3lab-shell-index-NEW.js']);
  });

  it('keeps itself alive until a fetched asset is in the cache', async () => {
    const { listeners, stores } = load('index-NEW.js', []);
    const kept: Promise<unknown>[] = [];
    let answered: Promise<Response> | undefined;
    listeners.fetch({
      request: new Request('https://host.ts.net/assets/calendar-1a2b.js'),
      respondWith: (response: Promise<Response>) => (answered = response),
      waitUntil: (work: Promise<unknown>) => kept.push(work),
    });
    await answered;
    expect(kept).toHaveLength(1);
    await Promise.all(kept);
    expect([...stores.get('mu3lab-shell-index-NEW.js')!.keys()]).toEqual(['/assets/calendar-1a2b.js']);
  });
});
