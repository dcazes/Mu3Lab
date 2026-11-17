import { cleanup, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { SystemResponse } from '../../api';
import { bytes } from '../../lib/format';
import { dashboardData, renderWithDashboard } from '../../test/fixtures';
import { ResourceCharts, buildShares } from './ResourceCharts';
import { lowOnSpace } from './warnings';

const GB = 1e9;

// This computer on 2026-11-03: Kopia's backups moved to the Storage drive.
function system(drives = true): SystemResponse {
  const base = dashboardData([]).system;
  return {
    ...base,
    memory: { total: 62.7 * GB, used: 12.4 * GB, percent: 20 },
    disk: { total: 467.9 * GB, used: 313 * GB, percent: 67 },
    drives: drives
      ? [{ mount: '/mnt/storage', name: 'Storage', role: 'backups', total: 566.5 * GB, used: 77.2 * GB, percent: 14 }]
      : [],
    disk_usage: {
      measured_at: new Date().toISOString(),
      owners: {
        immich: { data: 79 * GB, program: 1 * GB },
        'kopia-ui': { data: 0, program: 0.3 * GB },
        core: { data: 0.5 * GB, program: 2.2 * GB },
      },
      core_parts: {},
      removed_folders: [],
      drives: drives ? { '/mnt/storage': { 'kopia-ui': { data: 77.16 * GB, program: 0 } } } : {},
    },
  };
}

afterEach(cleanup);

describe('disk on two drives', () => {
  it('counts an app on every drive in the list, but each drive only in its own donut', () => {
    const { apps, last } = buildShares(system(), []);
    const kopia = apps.find((share) => share.key === 'kopia-ui')!;
    expect(kopia.disk).toBeCloseTo(77.46 * GB, -6);
    expect(kopia.diskMain).toBeCloseTo(0.3 * GB, -6);
    expect(kopia.diskOn).toEqual({ '/mnt/storage': 77.16 * GB });
    const outside = last.find((share) => share.key === 'outside')!;
    // The main drive's other use is what no app accounts for there; Storage holds almost nothing else.
    expect(outside.diskMain).toBeCloseTo((313 - 80 - 0.3 - 2.7) * GB, -6);
    expect(outside.diskOn!['/mnt/storage']).toBeCloseTo(0.04 * GB, -6);
  });

  it('shows a donut per drive and notes which drive holds an app’s data', () => {
    renderWithDashboard(<ResourceCharts />, dashboardData([], { system: system() }));
    expect(screen.getByText('Main drive')).toBeTruthy();
    expect(screen.getByText('Storage (backups)')).toBeTruthy();
    const row = screen.getByText('kopia-ui').closest('tr')!;
    expect(within(row).getByText(`${bytes(77.16 * GB)} on Storage`)).toBeTruthy();
    const free = screen.getByText('Free').closest('tr')!;
    expect(within(free).getByText(/on Storage/).textContent).toBe(`${bytes((566.5 - 77.2) * GB)} on Storage`);
  });

  it('keeps one Disk donut while everything is on the main drive', () => {
    renderWithDashboard(<ResourceCharts />, dashboardData([], { system: system(false) }));
    expect(screen.getByText('Disk', { selector: 'figcaption' })).toBeTruthy();
    expect(screen.queryByText('Main drive')).toBeNull();
    expect(screen.queryByText(/on Storage/)).toBeNull();
  });
});

describe('low on space', () => {
  it('warns under 10% free, or under 20 GB on a small drive', () => {
    expect(lowOnSpace({ total: 467.9 * GB, used: 313 * GB })).toBe(false);
    expect(lowOnSpace({ total: 467.9 * GB, used: 430 * GB })).toBe(true);
    expect(lowOnSpace({ total: 100 * GB, used: 85 * GB })).toBe(true);
    expect(lowOnSpace({ total: 0, used: 0 })).toBe(false);
  });
});
