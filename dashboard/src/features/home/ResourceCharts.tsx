import { Sparkles } from 'lucide-react';
import { useId, useState } from 'react';
import type { JobResponse, Service, SystemResponse } from '../../api';
import { postJsonApi } from '../../api/client';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { bytes, relativeTime } from '../../lib/format';
import { Link, navigate } from '../../lib/router';
import { useAction } from '../../lib/useAction';
import { useDashboard, useIsAdmin } from '../../state/dashboard';

/** One legend row and (when it has a colour of its own) one slice in each donut. */
export interface Share {
  key: string;
  label: string;
  /** CSS colour, usually a token. */
  color: string;
  ram: number;
  disk: number;
  data?: number;
  program?: number;
  /** App page to open on click. */
  to?: string;
  hatched?: boolean;
  /** Listed in the legend, but drawn as part of "Other apps". */
  folded?: boolean;
  /** Drawn, but not listed: its apps already have rows of their own. */
  unlisted?: boolean;
}

const SERIES = 8;
const HOME_APPS = 6;
const MIN_FREEABLE = 100 * 1024 ** 2;

/**
 * Apps, ranked by their combined share of memory and disk, so each keeps the
 * same colour in both charts and on both pages. Home shows the biggest six;
 * System lists every app and colours the biggest eight.
 */
export function buildShares(system: SystemResponse, services: Service[], detailed: boolean) {
  const ram = system.memory_by_owner || {};
  const disk = system.disk_usage?.owners || {};
  const special = new Set(['core', 'removed', 'unused']);
  const ids = [...new Set([...Object.keys(ram), ...Object.keys(disk)])].filter((id) => !special.has(id));
  const name = (id: string) => services.find((service) => service.id === id)?.name || id;
  const ramTotal = system.memory.total || 1;
  const diskTotal = system.disk.total || 1;
  const sized = (id: string) => ({
    ram: ram[id] || 0,
    data: disk[id]?.data || 0,
    program: disk[id]?.program || 0,
    disk: (disk[id]?.data || 0) + (disk[id]?.program || 0),
  });
  const apps = ids
    .map((id) => ({ key: id, label: name(id), to: `/apps/${id}`, ...sized(id) }))
    .filter((app) => app.ram || app.disk)
    .sort((a, b) => b.ram / ramTotal + b.disk / diskTotal - (a.ram / ramTotal + a.disk / diskTotal));
  const coloured = detailed ? SERIES : HOME_APPS;
  const shares: Share[] = apps.slice(0, detailed ? apps.length : coloured).map((app, index) => ({
    ...app,
    color: index < coloured ? `var(--series-${index + 1})` : 'var(--chart-other-apps)',
    folded: index >= coloured,
  }));
  const rest = apps.slice(coloured);
  // Removed apps' memory (a leftover still running) counts with the other apps.
  const otherApps = {
    ram: rest.reduce((sum, app) => sum + app.ram, 0) + (ram.removed || 0),
    disk: rest.reduce((sum, app) => sum + app.disk, 0),
  };
  if (otherApps.ram || otherApps.disk)
    shares.push({
      key: 'other-apps',
      label: `Other apps${rest.length ? ` (${rest.length})` : ''}`,
      unlisted: detailed,
      color: 'var(--chart-other-apps)',
      ...otherApps,
    });
  const core = sized('core');
  shares.push({ key: 'core', label: 'Mu3Lab core', color: 'var(--chart-core)', ...core });
  const freeable = (disk.removed?.data || 0) + (disk.removed?.program || 0) + (disk.unused?.program || 0);
  if (freeable >= MIN_FREEABLE)
    shares.push({
      key: 'freeable',
      label: 'Can be freed',
      color: 'var(--chart-core)',
      ram: 0,
      disk: freeable,
      hatched: true,
    });
  const counted = (pick: (share: Share) => number) =>
    shares.filter((share) => !share.folded).reduce((sum, share) => sum + pick(share), 0);
  shares.push({
    key: 'outside',
    label: 'Other (not Mu3Lab)',
    color: 'var(--chart-outside)',
    ram: Math.max(0, system.memory.used - counted((share) => share.ram)),
    disk: system.disk_usage ? Math.max(0, system.disk.used - counted((share) => share.disk)) : 0,
  });
  return { shares, freeable, rest };
}

function Donut({
  label,
  total,
  used,
  shares,
  pick,
  hovered,
  setHovered,
}: {
  label: string;
  total: number;
  used: number;
  shares: Share[];
  pick: (share: Share) => number;
  hovered: string;
  setHovered: (key: string) => void;
}) {
  const hatch = useId();
  const size = 120;
  const stroke = 16;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const visible = shares.filter((share) => !share.folded && pick(share) > 0);
  // Each slice starts where the ones before it end.
  const drawn = visible.map((share, index) => ({
    share,
    length: (pick(share) / (total || 1)) * circumference,
    start: visible.slice(0, index).reduce((sum, earlier) => sum + (pick(earlier) / (total || 1)) * circumference, 0),
  }));
  const focus = visible.find((share) => share.key === hovered);
  return (
    <figure className="donut">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="img"
        aria-label={`${label}: ${bytes(used)} of ${bytes(total)} used`}
      >
        <defs>
          <pattern id={hatch} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="5" height="5" fill="var(--chart-track)" />
            <line x1="0" y1="0" x2="0" y2="5" stroke="var(--chart-core)" strokeWidth="2.5" />
          </pattern>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--chart-track)" strokeWidth={stroke} />
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          {drawn.map(({ share, length, start }) => {
            // A 2px gap of surface between neighbouring slices.
            const gap = drawn.length > 1 && length > 4 ? 2 : 0;
            return (
              <circle
                key={share.key}
                className={`donut-slice${share.to ? ' donut-link' : ''}${hovered && hovered !== share.key ? ' dim' : ''}`}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke={share.hatched ? `url(#${hatch})` : share.color}
                strokeWidth={stroke}
                strokeDasharray={`${Math.max(0.5, length - gap)} ${circumference}`}
                strokeDashoffset={-start}
                onMouseEnter={() => setHovered(share.key)}
                onMouseLeave={() => setHovered('')}
                onClick={share.to ? () => navigate(share.to!) : undefined}
              >
                <title>{`${share.label}: ${bytes(pick(share))}`}</title>
              </circle>
            );
          })}
        </g>
      </svg>
      <div className="donut-centre" aria-hidden="true">
        <b>{bytes(focus ? pick(focus) : used)}</b>
        <small>{focus ? focus.label : `of ${bytes(total)}`}</small>
      </div>
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function FreeUpSpace({ freeable, folders }: { freeable: number; folders: string[] }) {
  const confirm = useConfirm();
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const free = async () => {
    if (
      !(await confirm({
        title: `Free up about ${bytes(freeable)}?`,
        description:
          'Mu3Lab removes old versions of your apps that nothing uses, and the programs of apps it no longer offers. The version before each app’s current one stays, so updates can still be undone.',
        confirmLabel: 'Free up space',
      }))
    )
      return;
    const deleteData =
      folders.length > 0 &&
      (await confirm({
        title: 'Also delete data that removed apps left behind?',
        description: `These folders belong to apps Mu3Lab no longer offers: ${folders.join(', ')}. Deleting them can’t be undone. Choose Cancel to keep them.`,
        confirmLabel: 'Delete their data',
        tone: 'danger',
      }));
    await run(
      'free',
      () => postJsonApi<JobResponse>('/api/v1/system/cleanup', { delete_data: deleteData }),
      'Freeing up space. The chart updates when it’s done.',
    );
    void refresh();
  };
  return (
    <Button size="sm" icon={Sparkles} loading={pending === 'free'} onClick={() => void free()}>
      Free up space
    </Button>
  );
}

/** Memory and disk per app as two donuts with one shared legend (Home's System chip, Settings > System). */
export function ResourceCharts({ detailed = false }: { detailed?: boolean }) {
  const { data } = useDashboard();
  const isAdmin = useIsAdmin();
  const [hovered, setHovered] = useState('');
  const { system } = data;
  const { shares, freeable } = buildShares(system, data.services.services, detailed);
  const measured = system.disk_usage?.measured_at;
  const focus = shares.find((share) => share.key === hovered);
  const caption = focus
    ? `${focus.label}: ${bytes(focus.ram)} memory · ${bytes(focus.disk)} disk${
        focus.program !== undefined ? ` (data ${bytes(focus.data || 0)} · program ${bytes(focus.program)})` : ''
      }`
    : measured
      ? `Memory is live. Disk was measured ${relativeTime(measured)}.`
      : 'Disk use per app is being measured; this takes a few minutes after setup.';
  return (
    <div className="resources">
      <div className="donuts">
        <Donut
          label="Memory"
          total={system.memory.total}
          used={system.memory.used}
          shares={shares}
          pick={(share) => share.ram}
          hovered={hovered}
          setHovered={setHovered}
        />
        <Donut
          label="Disk"
          total={system.disk.total}
          used={system.disk.used}
          shares={system.disk_usage ? shares : []}
          pick={(share) => share.disk}
          hovered={hovered}
          setHovered={setHovered}
        />
      </div>
      <p className="resources-caption" aria-live="polite">
        {caption}
      </p>
      <table className="resource-legend">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">App</span>
            </th>
            <th scope="col">Memory</th>
            <th scope="col">Disk</th>
          </tr>
        </thead>
        <tbody>
          {shares
            .filter((share) => !share.unlisted)
            .map((share) => (
              <tr
                key={share.key}
                className={hovered === share.key ? 'active' : ''}
                onMouseEnter={() => setHovered(share.key)}
                onMouseLeave={() => setHovered('')}
              >
                <th scope="row">
                  <span
                    className={`legend-swatch${share.hatched ? ' hatched' : ''}`}
                    style={{ background: share.hatched ? undefined : share.color }}
                    aria-hidden="true"
                  />
                  {share.to ? <Link to={share.to}>{share.label}</Link> : share.label}
                </th>
                <td>{share.ram ? bytes(share.ram) : '—'}</td>
                <td
                  title={
                    share.program !== undefined && share.disk
                      ? `Data ${bytes(share.data || 0)} · program ${bytes(share.program)}`
                      : undefined
                  }
                >
                  {share.disk ? bytes(share.disk) : '—'}
                  {detailed && share.program !== undefined && share.disk > 0 && (
                    <small className="legend-split">
                      {bytes(share.data || 0)} data · {bytes(share.program)} program
                    </small>
                  )}
                </td>
              </tr>
            ))}
          <tr className="legend-free">
            <th scope="row">
              <span className="legend-swatch track" aria-hidden="true" />
              Free
            </th>
            <td>{bytes(Math.max(0, system.memory.total - system.memory.used))}</td>
            <td>{bytes(Math.max(0, system.disk.total - system.disk.used))}</td>
          </tr>
        </tbody>
      </table>
      {isAdmin && freeable >= MIN_FREEABLE && (
        <div className="resources-free">
          <span>
            <b>{bytes(freeable)}</b> can be freed: old app versions and apps Mu3Lab no longer offers.
          </span>
          <FreeUpSpace freeable={freeable} folders={system.disk_usage?.removed_folders || []} />
        </div>
      )}
    </div>
  );
}
