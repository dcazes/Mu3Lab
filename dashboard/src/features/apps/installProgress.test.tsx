import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ImageDownload, InstallBatch, InstallBatchItem, Service } from '../../api';
import { DownloadStatus, InstallProgress } from './InstallProgress';

afterEach(cleanup);

const download = (extra: Partial<ImageDownload>): ImageDownload => ({
  state: 'downloading',
  total_bytes: 2 * 1024 ** 3,
  done_bytes: 1024 ** 3,
  rate_bps: 10 * 1024 ** 2,
  images_total: 2,
  images_done: 0,
  connections: 16,
  updated_at: '',
  ...extra,
});

describe('DownloadStatus', () => {
  it('shows percent, amount, speed and time left', () => {
    render(<DownloadStatus download={download({})} />);
    expect(screen.getByRole('progressbar', { name: 'Download progress' })).toHaveAttribute('aria-valuenow', '50');
    expect(screen.getByText('50% · 1.0 GB of 2.0 GB · 10.0 MB/s · about 2 min left')).toBeInTheDocument();
  });

  it('leaves out speed until it is known', () => {
    render(<DownloadStatus download={download({ rate_bps: 0, done_bytes: 0 })} />);
    expect(screen.getByText('0% · 0 B of 2.0 GB')).toBeInTheDocument();
  });

  it('says when Docker is unpacking or doing the download itself', () => {
    render(<DownloadStatus download={download({ state: 'loading', images_done: 1 })} />);
    expect(screen.getByText('Unpacking into Docker (1 of 2)…')).toBeInTheDocument();
    cleanup();
    render(<DownloadStatus download={download({ state: 'docker' })} />);
    expect(screen.getByText('Downloading with Docker…')).toBeInTheDocument();
  });
});

const item = (service_id: string, ordinal: number, extra: Partial<InstallBatchItem> = {}): InstallBatchItem => ({
  batch_id: 'b1',
  service_id,
  ordinal,
  explicitly_selected: 1,
  state: 'pending',
  job_id: '',
  started_at: '',
  completed_at: '',
  priority: ordinal,
  download_state: '',
  download: null,
  ...extra,
});

const batch = (items: InstallBatchItem[]): InstallBatch => ({
  id: 'b1',
  actor: 'owner',
  state: 'running',
  current_ordinal: 0,
  parallel_downloads: 3,
  created_at: '',
  updated_at: '',
  items,
});

const services = ['mealie', 'surfsense', 'immich'].map((id) => ({
  id,
  name: id[0].toUpperCase() + id.slice(1),
})) as Service[];

function renderProgress(items: InstallBatchItem[]) {
  const controls = { pause: vi.fn(), resume: vi.fn(), reorder: vi.fn(), setParallel: vi.fn() };
  render(
    <InstallProgress
      batch={batch(items)}
      job={null}
      services={services}
      onAction={vi.fn()}
      onDismiss={vi.fn()}
      controls={controls}
    />,
  );
  return controls;
}

describe('InstallProgress controls', () => {
  it('pauses a download and resumes a paused one', () => {
    const controls = renderProgress([
      item('mealie', 0, { download_state: 'downloading', download: download({}) }),
      item('surfsense', 1, { download_state: 'paused' }),
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(controls.pause).toHaveBeenCalledWith('mealie');
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    expect(controls.resume).toHaveBeenCalledWith('surfsense');
  });

  it('moves apps that are not being set up yet', () => {
    const controls = renderProgress([
      item('mealie', 0, { state: 'running', job_id: 'j1' }),
      item('surfsense', 1),
      item('immich', 2),
    ]);
    expect(screen.queryByRole('button', { name: 'Move Mealie down' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Move Surfsense up' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Move Immich up' }));
    expect(controls.reorder).toHaveBeenCalledWith(['mealie', 'immich', 'surfsense']);
  });

  it('keeps the download limit in the install dialog only', () => {
    renderProgress([item('mealie', 0)]);
    expect(screen.queryByLabelText('Downloads at once')).not.toBeInTheDocument();
  });

  it('says what each waiting app is doing', () => {
    renderProgress([
      item('mealie', 0, { download_state: 'ready' }),
      item('surfsense', 1),
      item('immich', 2, { download_state: 'paused', download: download({ done_bytes: 1024 ** 3 }) }),
    ]);
    expect(screen.getByText('Downloaded · waiting for its turn to set up')).toBeInTheDocument();
    expect(screen.getByText('Waiting to download')).toBeInTheDocument();
    expect(screen.getByText('Paused at 50% · 1.0 GB of 2.0 GB')).toBeInTheDocument();
  });
});
