import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AiModels } from '../../api';
import { aiModels, dashboardData, renderWithDashboard, stubFetch } from '../../test/fixtures';
import { AiModelsCard, whereChatRuns } from './AiModelsCard';

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
    if (path === '/api/v1/jobs/job1')
      return { ok: true, job: { id: 'job1', state: 'running', step_id: 'download', detail: '' }, events: [] };
    return undefined;
  });
  renderWithDashboard(<AiModelsCard />, dashboardData([]));
  return puts;
}

async function confirmDialog() {
  fireEvent.click(await screen.findByRole('button', { name: 'Change' }));
}

describe('Where AI runs', () => {
  it('says plainly where chat goes for each setting', () => {
    expect(whereChatRuns(aiModels())).toBe('Through your outside providers. No local chat model is chosen.');
    expect(whereChatRuns(aiModels({ chat_model: 'qwen3.5-4b' }))).toContain(
      'Qwen 3.5 4B (private) runs on this server',
    );
    expect(whereChatRuns(aiModels({ privacy: 'local_only', chat_model: 'qwen3.5-4b' }))).toBe(
      'Only on this server, with Qwen 3.5 4B. Nothing is sent to outside providers.',
    );
  });

  it('will not save local only without a local chat model', async () => {
    setup();
    fireEvent.click(await screen.findByLabelText(/Local only/));
    expect(screen.getByText('Local only needs a local chat model.')).toBeInTheDocument();
    expect((screen.getByRole('button', { name: 'Save and apply' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('switches to local only after confirming, and follows the job', async () => {
    const puts = setup();
    fireEvent.click(await screen.findByLabelText(/Local only/));
    fireEvent.change(screen.getByLabelText('Local chat model'), { target: { value: 'qwen3.5-4b' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save and apply' }));
    expect(await screen.findByText(/Outside provider keys stay saved but are not used/)).toBeInTheDocument();
    expect(screen.getByText(/downloaded first \(3.3 GB\)/)).toBeInTheDocument();
    await confirmDialog();
    await vi.waitFor(() =>
      expect(puts).toEqual([{ privacy: 'local_only', chat_model: 'qwen3.5-4b', embedding_model: 'bge-m3' }]),
    );
    expect(await screen.findByText('Changing AI models: Download')).toBeInTheDocument();
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
    renderWithDashboard(<AiModelsCard />, dashboardData([]));
    expect(await screen.findByText('Nothing was changed: out of memory.')).toBeInTheDocument();
  });
});
