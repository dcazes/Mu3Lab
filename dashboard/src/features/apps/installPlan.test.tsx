import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AppSizesResponse } from '../../api';
import { InstallPlanDialog, sizeLabel } from './InstallPlanDialog';

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const size = (needed: number, download = needed) => ({
  download_bytes: download,
  needed_bytes: needed,
  disk_bytes: download * 4,
  needed_disk_bytes: needed * 4,
});

const sizes: AppSizesResponse = {
  apps: {
    surfsense: { ...size(2 * 1024 ** 3), updated_at: '', complete: true },
    mealie: { ...size(400 * 1024 ** 2), updated_at: '', complete: true },
  },
  selection: size(2.4 * 1024 ** 3),
  measured: true,
  free_bytes: 200 * 1024 ** 3,
};

function renderDialog(onInstall = vi.fn(), ids = ['surfsense', 'mealie', 'immich']) {
  render(
    <InstallPlanDialog
      ids={ids}
      names={{ surfsense: 'SurfSense', mealie: 'Mealie', immich: 'Immich' }}
      sizes={sizes}
      pending={false}
      onClose={vi.fn()}
      onInstall={onInstall}
    />,
  );
  return onInstall;
}

describe('InstallPlanDialog', () => {
  it('lists the smallest download first and apps still being measured last', () => {
    renderDialog();
    expect(screen.getAllByRole('listitem').map((item) => item.querySelector('b')?.textContent)).toEqual([
      'Mealie',
      'SurfSense',
      'Immich',
    ]);
    expect(screen.getByText('Checking size…')).toBeInTheDocument();
    expect(screen.getByText('2.4 GB to download · about 9.6 GB of disk space · 200.0 GB free')).toBeInTheDocument();
  });

  it('installs in the order the owner chose, remembering downloads at once', () => {
    const onInstall = renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Move SurfSense up' }));
    fireEvent.change(screen.getByLabelText('Downloads at once'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Install all selected' }));
    expect(onInstall).toHaveBeenCalledWith(['surfsense', 'mealie', 'immich'], 2);
    expect(localStorage.getItem('mu3lab.parallelDownloads')).toBe('2');
  });

  it('never offers more downloads at once than apps chosen', () => {
    localStorage.setItem('mu3lab.parallelDownloads', '6');
    const onInstall = renderDialog(vi.fn(), ['surfsense', 'mealie']);
    const select = screen.getByLabelText<HTMLSelectElement>('Downloads at once');
    expect([...select.options].map((option) => option.value)).toEqual(['1', '2']);
    fireEvent.click(screen.getByRole('button', { name: 'Install all selected' }));
    expect(onInstall).toHaveBeenCalledWith(['mealie', 'surfsense'], 2);
    // Untouched, the owner's saved preference is kept for bigger selections.
    expect(localStorage.getItem('mu3lab.parallelDownloads')).toBe('6');
  });

  it('hides the choice for a single app', () => {
    renderDialog(vi.fn(), ['mealie']);
    expect(screen.queryByLabelText('Downloads at once')).not.toBeInTheDocument();
  });
});

describe('sizeLabel', () => {
  it('says what is left to download when part of an app is already here', () => {
    expect(sizeLabel(size(0, 1024 ** 3))).toBe('Already downloaded · about 4.0 GB on disk');
    expect(sizeLabel(size(100 * 1024 ** 2, 1024 ** 3))).toBe('100.0 MB left to download · about 4.0 GB on disk');
    expect(sizeLabel(undefined)).toBe('Checking size…');
  });
});
