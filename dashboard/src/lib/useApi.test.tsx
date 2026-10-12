import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useApi } from './useApi';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('useApi (review R22)', () => {
  it("never shows one resource's data as another's, and aborts the request no longer needed", async () => {
    const answers: Record<string, (body: unknown) => void> = {};
    const aborted: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (path: string, init: RequestInit) =>
          new Promise<Response>((resolve, reject) => {
            answers[path] = (body) => resolve(new Response(JSON.stringify(body), { status: 200 }));
            init.signal?.addEventListener('abort', () => {
              aborted.push(path);
              reject(new DOMException('aborted', 'AbortError'));
            });
          }),
      ),
    );
    const { result, rerender } = renderHook(({ path }: { path: string | null }) => useApi<{ name: string }>(path), {
      initialProps: { path: '/api/v1/apps/a' as string | null },
    });
    await act(async () => answers['/api/v1/apps/a']({ name: 'A' }));
    expect(result.current.data).toEqual({ name: 'A' });

    rerender({ path: '/api/v1/apps/b' });
    expect(result.current).toMatchObject({ data: null, loading: true });
    rerender({ path: '/api/v1/apps/c' });
    expect(aborted).toContain('/api/v1/apps/b');
    await act(async () => answers['/api/v1/apps/c']({ name: 'C' }));
    await waitFor(() => expect(result.current.data).toEqual({ name: 'C' }));

    rerender({ path: null });
    expect(result.current).toMatchObject({ data: null, loading: false });
  });
});
