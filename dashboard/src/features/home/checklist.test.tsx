import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubFetch } from '../../test/fixtures';
import { useGetStartedChecklist } from './GetStarted';

beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('shared checklist', () => {
  it('migrates old ticks without replacing another device’s progress', async () => {
    localStorage.setItem('mu3lab.getStarted', JSON.stringify({ devices: true, chat: true, unknown: true }));
    let items: Record<string, boolean> = { extension: true };
    const fetch = stubFetch((path, init) => {
      if (path !== '/api/v1/me/checklist') return undefined;
      if (init?.method === 'PUT') items = { ...items, ...JSON.parse(String(init.body)).items };
      return { ok: true, items };
    });
    const { result } = renderHook(useGetStartedChecklist);
    await waitFor(() => expect(result.current[0]).toEqual({ extension: true, devices: true, chat: true }));
    expect(localStorage.getItem('mu3lab.getStarted')).toBeNull();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
    act(() => result.current[1]('hidden'));
    await waitFor(() => expect(result.current[0].hidden).toBe(true));
  });

  it('keeps old ticks when saving them fails', async () => {
    localStorage.setItem('mu3lab.getStarted', JSON.stringify({ devices: true }));
    stubFetch((path, init) =>
      path === '/api/v1/me/checklist'
        ? init?.method === 'PUT'
          ? new Response(JSON.stringify({ error: 'Unavailable' }), { status: 503 })
          : { ok: true, items: {} }
        : undefined,
    );
    const { result } = renderHook(useGetStartedChecklist);
    await waitFor(() => expect(result.current[0]).toEqual({}));
    expect(localStorage.getItem('mu3lab.getStarted')).toBe(JSON.stringify({ devices: true }));
  });
});
