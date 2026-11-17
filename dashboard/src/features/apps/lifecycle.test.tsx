import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Service } from '../../api';
import { dashboardData, renderWithDashboard, service, stubFetch } from '../../test/fixtures';
import { HomePage } from '../home/HomePage';
import { inOrder, neededToStart, runningDependents, unneededAfterStop } from './lifecycle';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const stopped: Partial<Service> = { display_state: 'stopped', allowed_actions: ['start', 'uninstall'] };

/** SurfSense needs LiteLLM, which needs Ollama and FreeLLMAPI; Open WebUI needs LiteLLM too. */
function apps(overrides: Record<string, Partial<Service>> = {}) {
  return [
    service('ollama', 'Ollama', 'core', overrides.ollama),
    service('freellmapi', 'FreeLLMAPI', 'core', overrides.freellmapi),
    service('litellm', 'LiteLLM', 'core', { dependencies: ['ollama', 'freellmapi'], ...overrides.litellm }),
    service('open-webui', 'Open WebUI', 'core', { dependencies: ['litellm'], ...overrides['open-webui'] }),
    service('surfsense', 'SurfSense', 'optional', { dependencies: ['litellm'], ...overrides.surfsense }),
  ];
}

const ids = (list: Service[]) => list.map((item) => item.id);
const pick = (all: Service[], ...wanted: string[]) => all.filter((item) => wanted.includes(item.id));

describe('apps that need each other', () => {
  it('starts what an app needs first, theirs before its own', () => {
    const all = apps({ ollama: stopped, freellmapi: stopped, litellm: stopped, surfsense: stopped });
    expect(ids(neededToStart(pick(all, 'surfsense'), all))).toEqual(['ollama', 'freellmapi', 'litellm']);
    expect(ids(inOrder(pick(all, 'surfsense', 'litellm'), all))).toEqual(['litellm', 'surfsense']);
  });

  it('warns about running apps that need the one stopping', () => {
    const all = apps({ surfsense: stopped });
    expect(ids(runningDependents(pick(all, 'ollama'), all))).toEqual(['litellm', 'open-webui']);
  });

  it('offers only helpers nothing else running needs', () => {
    // Open WebUI still uses LiteLLM, and so Ollama and FreeLLMAPI.
    expect(ids(unneededAfterStop(pick(apps(), 'surfsense'), apps()))).toEqual([]);
    const alone = apps({ 'open-webui': stopped });
    expect(ids(unneededAfterStop(pick(alone, 'surfsense'), alone))).toEqual(['ollama', 'freellmapi', 'litellm']);
  });
});

describe('Home select mode', () => {
  it('picks apps by highlighting them and starts them with what they need', async () => {
    const all = apps({ ollama: stopped, freellmapi: stopped, litellm: stopped, surfsense: stopped });
    const fetchMock = stubFetch((path) => (path.endsWith('/actions') ? { ok: true, job: { id: 'j' } } : undefined));
    renderWithDashboard(<HomePage />, dashboardData(all));
    fireEvent.click(screen.getByRole('button', { name: 'Select' }));
    const tile = screen.getByRole('button', { name: /^SurfSense/ });
    fireEvent.click(tile);
    expect(tile).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByText('SurfSense needs these stopped apps')).toBeInTheDocument();
    // Leave FreeLLMAPI stopped.
    fireEvent.click(screen.getByRole('button', { name: 'FreeLLMAPI', pressed: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Start 3 apps' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/services/surfsense/actions',
        expect.objectContaining({ body: JSON.stringify({ action: 'start', dependencies: ['ollama', 'litellm'] }) }),
      ),
    );
  });
});
