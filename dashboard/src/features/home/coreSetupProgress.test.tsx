import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { CoreAppProgress, Job } from '../../api';
import { dashboardData, renderWithDashboard } from '../../test/fixtures';
import { CoreSetupProgress } from './CoreSetupProgress';

afterEach(cleanup);

const job = (state: string): Job => ({
  id: 'core',
  kind: 'lifecycle',
  service_id: 'core-suite',
  action: 'install',
  actor: 'bootstrap',
  state,
  detail: '',
  created_at: '2026-10-15T10:00:00Z',
  updated_at: '2026-10-15T10:00:00Z',
});

const apps: CoreAppProgress[] = [
  { service_id: 'ollama', name: 'Ollama', state: 'ready' },
  {
    service_id: 'open-webui',
    name: 'Open WebUI',
    state: 'downloading',
    download: {
      state: 'downloading',
      total_bytes: 4e9,
      done_bytes: 1e9,
      rate_bps: 10e6,
      images_total: 1,
      images_done: 0,
      connections: 3,
      updated_at: '',
    },
  },
  { service_id: 'litellm', name: 'LiteLLM', state: 'waiting' },
];

function renderCore(state: string) {
  const data = dashboardData([]);
  return renderWithDashboard(
    <CoreSetupProgress />,
    dashboardData([], { core: { ...data.core, current_job: job(state), apps } }),
  );
}

describe('core setup progress', () => {
  it('shows each app and the one downloading while setup runs', () => {
    renderCore('running');
    expect(screen.getByText('1 of 3 ready')).toBeTruthy();
    expect(screen.getByText('Waiting to download')).toBeTruthy();
    expect(screen.getByRole('progressbar', { name: 'Download progress' }).getAttribute('aria-valuenow')).toBe('25');
  });

  it('disappears once setup has finished', () => {
    renderCore('succeeded');
    expect(screen.queryByText('Setting up your AI apps')).toBeNull();
  });
});
