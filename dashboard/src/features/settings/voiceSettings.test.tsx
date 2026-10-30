import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { VoiceApps, VoicePage } from '../../api';
import { aiModels, dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { VoiceSettings } from './VoiceSettings';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function voicePage(overrides: Partial<VoicePage['settings']> = {}, searchInstalled = true): VoicePage {
  return {
    ok: true,
    installed: true,
    settings: {
      responsiveness: 'balanced',
      interruptions: true,
      voice: '',
      search: true,
      changed_at: '',
      changed_by: '',
      ...overrides,
    },
    speech: {
      app_id: 'speaches',
      installed: true,
      stt: {
        value: 'deepdml/faster-whisper-large-v3-turbo-ct2',
        options: ['deepdml/faster-whisper-large-v3-turbo-ct2', 'Systran/faster-whisper-small'],
      },
      tts: {
        value: 'speaches-ai/Kokoro-82M-v1.0-ONNX',
        options: ['speaches-ai/Kokoro-82M-v1.0-ONNX-int8', 'speaches-ai/Kokoro-82M-v1.0-ONNX'],
      },
      default_voice: 'af_heart',
    },
    voices: [
      { id: 'af_heart', name: 'Heart', accent: 'American', gender: 'female' },
      { id: 'bm_george', name: 'George', accent: 'British', gender: 'male' },
    ],
    search_installed: searchInstalled,
    search_app_id: 'searxng',
    active_job_id: '',
  };
}

function apps(on: boolean): VoiceApps {
  return {
    ok: true,
    installed: true,
    apps: [
      { id: 'beaver-habits', title: 'Beaver Habits', description: 'Habits.', operators_only: false, on },
      { id: 'kopia-ui', title: 'Backups', description: 'Backups.', operators_only: true, on: true },
    ],
  };
}

/** The whole page, answering every request it makes; ``puts`` collects what it saves. */
function renderPage({ page = voicePage(), appsOn = true } = {}) {
  const puts: { path: string; body: unknown }[] = [];
  let current = page;
  let currentApps = apps(appsOn);
  stubFetch((path, init) => {
    if (init?.method === 'PUT' || init?.method === 'POST') {
      const body = JSON.parse(String(init.body));
      puts.push({ path, body });
      if (path === '/api/v1/ai/voice/settings') {
        current = { ...current, settings: { ...current.settings, ...body } };
        return current;
      }
      if (path === '/api/v1/ai/voice/apps/beaver-habits') {
        currentApps = apps(body.on);
        return currentApps;
      }
      return { ok: true, job: { id: 'job-1', state: 'queued' } };
    }
    if (path === '/api/v1/ai/models') return aiModels();
    if (path === '/api/v1/ai/voice/settings') return current;
    if (path === '/api/v1/ai/voice/apps') return currentApps;
    if (path === '/api/v1/ai/voice/apps/check')
      return {
        ok: true,
        apps: [
          { id: 'beaver-habits', ok: true, tools: 12, detail: '' },
          { id: 'kopia-ui', ok: false, tools: 0, detail: 'ContextForge is not answering.' },
        ],
      };
    if (path === '/api/v1/voice-key')
      return { ok: true, available: true, exists: false, base_url: '', models: [], created_at: '' };
    return undefined;
  });
  renderWithDashboard(<VoiceSettings />, dashboardData([]));
  return puts;
}

describe('Voice assistant settings', () => {
  it('switches one app off for voice and shows whether each app answers', async () => {
    const puts = renderPage();
    const habits = await screen.findByRole('switch', { name: 'Voice can use Beaver Habits' });
    expect(habits).toBeChecked();
    expect(await screen.findByText('12 tools')).toBeInTheDocument();
    expect(screen.getByText('Not answering')).toBeInTheDocument();
    expect(screen.getByText('Backups: ContextForge is not answering.')).toBeInTheDocument();
    fireEvent.click(habits);
    await vi.waitFor(() =>
      expect(puts).toContainEqual({ path: '/api/v1/ai/voice/apps/beaver-habits', body: { on: false } }),
    );
    await vi.waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Voice can use Beaver Habits' })).not.toBeChecked(),
    );
  });

  it('turns web search off, and says when SearXNG is missing', async () => {
    const puts = renderPage({ page: voicePage({}, false) });
    expect(await screen.findByText('Needs SearXNG')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: 'Voice can search the web' }));
    await vi.waitFor(() => expect(puts).toContainEqual({ path: '/api/v1/ai/voice/settings', body: { search: false } }));
  });

  it('saves responsiveness and interruptions at once', async () => {
    const puts = renderPage();
    const group = await screen.findByRole('radiogroup', { name: 'Responsiveness' });
    expect(within(group).getByRole('radio', { name: 'Balanced' })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(within(group).getByRole('radio', { name: 'Fast' }));
    await vi.waitFor(() =>
      expect(puts).toContainEqual({ path: '/api/v1/ai/voice/settings', body: { responsiveness: 'fast' } }),
    );
    fireEvent.click(screen.getByRole('switch', { name: 'Let people interrupt' }));
    await vi.waitFor(() =>
      expect(puts).toContainEqual({ path: '/api/v1/ai/voice/settings', body: { interruptions: false } }),
    );
  });

  it('chooses the assistant’s voice without a restart', async () => {
    const puts = renderPage();
    const select = await screen.findByRole('combobox', { name: 'The assistant’s voice' });
    expect(within(select).getByRole('option', { name: 'Speaches’ default (Heart, American)' })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: 'bm_george' } });
    await vi.waitFor(() =>
      expect(puts).toContainEqual({ path: '/api/v1/ai/voice/settings', body: { voice: 'bm_george' } }),
    );
  });

  it('changes the speech models only after confirming Speaches is set up again', async () => {
    const puts = renderPage();
    const recognition = await screen.findByRole('combobox', { name: 'Hearing model' });
    fireEvent.change(recognition, { target: { value: 'Systran/faster-whisper-small' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save and set up Speaches' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Change models' }));
    await vi.waitFor(() =>
      expect(puts).toContainEqual({
        path: '/api/v1/ai/voice/speech',
        body: { stt_model: 'Systran/faster-whisper-small', tts_model: 'speaches-ai/Kokoro-82M-v1.0-ONNX' },
      }),
    );
  });

  it('offers the same answers as chat as one of the choices', async () => {
    renderPage();
    expect(await screen.findByRole('radio', { name: /Same as chat/ })).toBeChecked();
    expect(screen.getByText(/^The same as chat\./)).toBeInTheDocument();
  });

  it('keeps full-duplex mode folded away under Labs', async () => {
    renderPage();
    const labs = await screen.findByRole('button', { name: /Labs: full-duplex mode/ });
    expect(labs).toHaveAttribute('aria-expanded', 'false');
  });
});
