import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useDashboardLoader } from './dashboard';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('dashboard refresh', () => {
  it('lets a sign-out during a refresh win over the healthy probe', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) => {
        if (path === '/api/health') return new Response('{}', { status: 200 });
        // Authentik redirects every other request to its login page.
        return new Response(null, { status: 302, headers: { Location: '/login' } });
      }),
    );
    const { result } = renderHook(() => useDashboardLoader());
    await waitFor(() => expect(result.current.connection).toBe('signed_out'));
    expect(result.current.data.identity.writes_enabled).toBeFalsy();
  });
});
