import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AiModels } from '../../api';
import { aiModels, dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { useApi } from '../../lib/useApi';
import { AiModelsCard, whereChatRuns } from './AiModelsCard';

/** The AI page loads the models once and hands them to the card. */
function ModelsCard() {
  return <AiModelsCard models={useApi<AiModels>('/api/v1/ai/models')} />;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup(current: AiModels = aiModels()) {
  const puts: unknown[] = [];
  stubFetch((path, init) => {
    if (path === '/api/v1/ai/models') {
      if (init?.method === 'PUT') {
        puts.push(JSON.parse(String(init.body)));
        return { ok: true, job: { id: 'job1', state: 'queued' } };
      }
      return current;
    }
    if (path === '/api/v1/ai/models/try')
      return {
        ok: true,
        where: 'this server',
        name: 'Qwen 3.5 4B',
        answer: 'A home server keeps your things at home.',
        seconds: 4.2,
        loading_seconds: 1.5,
        tokens_per_second: 31.0,
      };
    if (path === '/api/v1/jobs/job1')
      return {
        ok: true,
        job: { id: 'job1', state: 'running', step_id: 'download', detail: '' },
        events: [{ detail: 'Downloading Qwen 3.5 4B into Ollama (3.3 GB).', created_at: '2026-10-20T12:00:00Z' }],
        // Ollama's byte count, as the worker stores it for the bar.
        download: {
          state: 'downloading',
          total_bytes: 3.3e9,
          done_bytes: 1.1e9,
          rate_bps: 2.2e7,
          images_total: 0,
          images_done: 0,
          connections: 0,
          updated_at: '',
        },
      };
    return undefined;
  });
  renderWithDashboard(<ModelsCard />, dashboardData([]));
  return puts;
}

async function confirmDialog() {
  fireEvent.click(await screen.findByRole('button', { name: 'Change' }));
}

describe('Where AI runs', () => {
  it('suggests what fits this computer, and tries a downloaded model', async () => {
    const current = aiModels();
    current.suggested = 'qwen3.5-4b';
    current.suggestion_reason = 'Qwen 3.5 4B fits in the GPU with room left for your other apps.';
    current.chat_models = current.chat_models.map((model) =>
      model.id === 'qwen3.5-4b'
        ? { ...model, downloaded: true, fit: 'good', fit_where: 'gpu' }
        : { ...model, fit: 'too_big', fit_where: '' },
    );
    setup(current);
    expect(await screen.findByText('Suggested for this computer: Qwen 3.5 4B')).toBeInTheDocument();
    const row = screen.getByRole('radio', { name: /Qwen 3\.5 4B/ }).closest('.model-entry') as HTMLElement;
    expect(row).toHaveTextContent('Suggested');
    expect(row).toHaveTextContent('Runs well here');
    expect(screen.getAllByText('Too big for this computer now').length).toBeGreaterThan(0);
    // Turning local models on picks the suggestion.
    fireEvent.click(screen.getByRole('checkbox', { name: /Local models/ }));
    expect(screen.getByRole('radio', { name: /Qwen 3\.5 4B/ })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Try Qwen 3.5 4B' }));
    expect(await screen.findByText('A home server keeps your things at home.')).toBeInTheDocument();
    expect(screen.getByText(/about 31 tokens a second/)).toBeInTheDocument();
  });

  it('says plainly where chat goes for each setting', () => {
    expect(whereChatRuns(aiModels())).toBe('Through your outside providers. Local models are off.');
    // A model kept while local models are off is not offered.
    expect(whereChatRuns(aiModels({ chat_model: 'qwen3.5-4b' }))).toBe(
      'Through your outside providers. Local models are off.',
    );
    expect(whereChatRuns(aiModels({ sources: ['outside', 'local'], chat_model: 'qwen3.5-4b' }))).toContain(
      'Qwen 3.5 4B (private) runs on this server',
    );
    expect(whereChatRuns(aiModels({ sources: ['local', 'outside'], chat_model: 'qwen3.5-4b' }))).toContain(
      'Chat runs on this server with Qwen 3.5 4B by default.',
    );
    expect(whereChatRuns(aiModels({ sources: ['local'], chat_model: 'qwen3.5-4b' }))).toBe(
      'Chat runs only on this server, with Qwen 3.5 4B. Chat sends nothing to outside providers.',
    );
  });

  it('will not turn local models on without a local chat model', async () => {
    setup();
    fireEvent.click(await screen.findByRole('checkbox', { name: /Local models/ }));
    expect(screen.getByText('Choose a local chat model to run chat on this server.')).toBeInTheDocument();
    expect((screen.getByRole('button', { name: 'Save and apply' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('switches to local only after confirming, and follows the job', async () => {
    const puts = setup();
    fireEvent.click(await screen.findByRole('checkbox', { name: /Local models/ }));
    fireEvent.click(screen.getByRole('radio', { name: /Qwen 3\.5 4B/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /Outside providers/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save and apply' }));
    expect(await screen.findByText(/Outside provider keys stay saved but are not used/)).toBeInTheDocument();
    expect(screen.getByText(/downloaded first \(3.3 GB\)/)).toBeInTheDocument();
    await confirmDialog();
    await vi.waitFor(() =>
      expect(puts).toEqual([
        { sources: ['local'], chat_model: 'qwen3.5-4b', embedding_model: 'bge-m3', runner: 'ollama' },
      ]),
    );
    // The model download shows a bar with how far it has got, as app downloads do.
    expect(
      await screen.findByText('Downloading Qwen 3.5 4B into Ollama (3.3 GB).', { selector: '.model-job-step' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Download progress' }).getAttribute('aria-valuenow')).toBe('33');
  });

  it('ranks local models and outside providers, and keeps one on', async () => {
    const puts = setup(aiModels({ sources: ['outside', 'local'], chat_model: 'qwen3-8b' }));
    const local = await screen.findByRole('checkbox', { name: /Local models/ });
    expect(local.closest('.source-row')).toHaveTextContent('Second, a choice in chat');
    fireEvent.click(screen.getByRole('button', { name: 'Put local models first' }));
    expect(local.closest('.source-row')).toHaveTextContent('First');
    fireEvent.click(screen.getByRole('button', { name: 'Save and apply' }));
    expect(await screen.findByText(/Chat will answer on this server with Qwen3 8B/)).toBeInTheDocument();
    await confirmDialog();
    await vi.waitFor(() =>
      expect(puts).toEqual([
        { sources: ['local', 'outside'], chat_model: 'qwen3-8b', embedding_model: 'bge-m3', runner: 'ollama' },
      ]),
    );
  });

  it('lets outside providers run alone, with no local model', async () => {
    setup();
    await screen.findByLabelText('Search model');
    // Chat needs somewhere to run, so the only source turned on cannot be turned off.
    expect(screen.getByRole('checkbox', { name: /Outside providers/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Outside providers/ })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /Local models/ })).not.toBeChecked();
    expect(screen.queryByLabelText('Model runner')).not.toBeInTheDocument();
    expect(screen.queryByText('Last changed')).not.toBeInTheDocument();
    expect(screen.queryByText('Changed by')).not.toBeInTheDocument();
  });

  it('offers no runner choice while Ollama is the only model runner', async () => {
    setup(aiModels({ sources: ['local'], chat_model: 'qwen3.5-4b' }));
    await screen.findByRole('group', { name: 'Local chat model' });
    expect(screen.queryByLabelText('Model runner')).not.toBeInTheDocument();
  });

  it('offers only installed model runners and says the new one downloads first', async () => {
    const installed = aiModels(
      { sources: ['local'], chat_model: 'qwen3.5-4b' },
      {
        runners: [
          { id: 'ollama', name: 'Ollama', installed: true, has_ui: false },
          { id: 'other', name: 'Other runner', installed: true, has_ui: false },
          { id: 'missing', name: 'Missing runner', installed: false, has_ui: false },
        ],
      },
    );
    const puts = setup(installed);
    const picker = (await screen.findByLabelText('Model runner')) as HTMLSelectElement;
    expect(picker.value).toBe('ollama');
    const missing = screen.getByRole('option', { name: /Missing runner \(install it from Apps first\)/ });
    expect((missing as HTMLOptionElement).disabled).toBe(true);
    fireEvent.change(picker, { target: { value: 'other' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save and apply' }));
    expect(await screen.findByText(/Other runner downloads the chosen models first/)).toBeInTheDocument();
    await confirmDialog();
    await vi.waitFor(() => expect(puts).toEqual([expect.objectContaining({ runner: 'other' })]));
  });

  it('warns that documents are indexed again when the search model changes', async () => {
    setup();
    fireEvent.change(await screen.findByLabelText('Search model'), { target: { value: 'nomic-embed-text' } });
    expect(screen.getByText('Documents need indexing again')).toBeInTheDocument();
  });

  it('locks the search model while an app keeps an index for it', async () => {
    setup(aiModels({}, { embedding_locked_by: ['SurfSense'] }));
    expect(((await screen.findByLabelText('Search model')) as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByText(/SurfSense keeps a search index/)).toBeInTheDocument();
  });

  it('shows why a change failed', async () => {
    stubFetch((path) =>
      path === '/api/v1/ai/models'
        ? aiModels({}, { active_job_id: 'job2' })
        : path === '/api/v1/jobs/job2'
          ? {
              ok: true,
              job: { id: 'job2', state: 'failed', step_id: 'download', detail: 'Nothing was changed: out of memory.' },
              events: [],
            }
          : undefined,
    );
    renderWithDashboard(<ModelsCard />, dashboardData([]));
    expect(await screen.findByText('Nothing was changed: out of memory.')).toBeInTheDocument();
  });
});
