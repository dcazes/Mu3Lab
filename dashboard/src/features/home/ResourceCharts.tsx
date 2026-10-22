import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Sparkles } from 'lucide-react';
import { useId, useState } from 'react';
import type { JobResponse, Service, SystemResponse } from '../../api';
import { postJsonApi } from '../../api/client';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { bytes, duration, relativeTime } from '../../lib/format';
import { jobActive } from '../../lib/jobs';
import { Link, navigate } from '../../lib/router';
import { useAction } from '../../lib/useAction';
import { useDashboard, useIsAdmin } from '../../state/dashboard';

/** One list row and (when it has a colour of its own) one slice in the memory, disk and graphics memory donuts. */
export interface Share {
  key: string;
  label: string;
  /** CSS colour, usually a token. */
  color: string;
  ram: number;
  disk: number;
  /** Graphics memory; 0 without an NVIDIA GPU. */
  gpu?: number;
  data?: number;
  program?: number;
  /** App page to open on click. */
  to?: string;
  hatched?: boolean;
  /** Mu3Lab core's parts, listed under it when it is unfolded. */
  parts?: Share[];
}

type SortKey = 'ram' | 'disk' | 'gpu';

const SERIES = 16;
const MIN_FREEABLE = 100 * 1024 ** 2;
// Core parts that are not catalog apps.
const PART_NAMES: Record<string, string> = {
  mu3lab: 'Mu3Lab dashboard and worker',
  'tailnet-relay': 'Private network relay',
};

/**
 * Every app, ranked by its combined share of memory and disk so it keeps the same colour in both
 * donuts and on both pages (sixteen colours, repeating after that); then Mu3Lab core with its
 * parts, and the rows that always come last: what can be freed and what is not Mu3Lab's.
 */
export function buildShares(system: SystemResponse, services: Service[]) {
  const ram = system.memory_by_owner || {};
  const disk = system.disk_usage?.owners || {};
  const gpu = system.gpu?.by_owner || {};
  const special = new Set(['core', 'removed', 'unused']);
  const ids = [...new Set([...Object.keys(ram), ...Object.keys(disk), ...Object.keys(gpu)])].filter(
    (id) => !special.has(id),
  );
  const name = (id: string) => services.find((service) => service.id === id)?.name || id;
  const ramTotal = system.memory.total || 1;
  const diskTotal = system.disk.total || 1;
  const sized = (memory: number, share?: { data: number; program: number }, graphics = 0) => ({
    ram: memory,
    gpu: graphics,
    data: share?.data || 0,
    program: share?.program || 0,
    disk: (share?.data || 0) + (share?.program || 0),
  });
  const apps: Share[] = ids
    .map((id) => ({
      key: id,
      label: name(id),
      to: `/apps/${id}`,
      color: '',
      ...sized(ram[id] || 0, disk[id], gpu[id] || 0),
    }))
    .filter((app) => app.ram || app.disk || app.gpu)
    .sort((a, b) => b.ram / ramTotal + b.disk / diskTotal - (a.ram / ramTotal + a.disk / diskTotal))
    .map((app, index) => ({
      ...app,
      color: `var(--series-${(index % SERIES) + 1})`,
    }));
  const partRam = system.memory_by_core_part || {};
  const partDisk = system.disk_usage?.core_parts || {};
  const partGpu = system.gpu?.by_core_part || {};
  const parts: Share[] = [...new Set([...Object.keys(partRam), ...Object.keys(partDisk), ...Object.keys(partGpu)])].map(
    (id) => ({
      key: `core-${id}`,
      label: PART_NAMES[id] || name(id),
      color: 'var(--chart-core)',
      ...sized(partRam[id] || 0, partDisk[id], partGpu[id] || 0),
    }),
  );
  const core: Share = {
    key: 'core',
    label: 'Mu3Lab core',
    color: 'var(--chart-core)',
    ...sized(ram.core || 0, disk.core, gpu.core || 0),
    parts,
  };
  const freeable = (disk.removed?.data || 0) + (disk.removed?.program || 0) + (disk.unused?.program || 0);
  const last: Share[] = [];
  if (freeable >= MIN_FREEABLE)
    last.push({
      key: 'freeable',
      label: 'Can be freed',
      color: 'var(--chart-core)',
      ram: 0,
      disk: freeable,
      hatched: true,
    });
  const counted = (pick: (share: Share) => number) =>
    [...apps, core, ...last].reduce((sum, share) => sum + pick(share), 0);
  // Includes a leftover still running from an app Mu3Lab no longer offers.
  last.push({
    key: 'outside',
    label: 'Other (not Mu3Lab)',
    color: 'var(--chart-outside)',
    ram: Math.max(0, system.memory.used - counted((share) => share.ram)),
    disk: system.disk_usage ? Math.max(0, system.disk.used - counted((share) => share.disk)) : 0,
    // The desktop and other programs on the GPU.
    gpu: system.gpu ? Math.max(0, system.gpu.used - counted((share) => share.gpu || 0)) : 0,
  });
  return { apps: [...apps, core], last, freeable };
}

function Donut({
  label,
  total,
  used,
  shares,
  pick,
  hovered,
  setHovered,
  format = bytes,
  detail,
}: {
  label: string;
  total: number;
  used: number;
  shares: Share[];
  pick: (share: Share) => number;
  hovered: string;
  setHovered: (key: string) => void;
  format?: (value: number) => string;
  /** Under the figure in the middle; "of <total>" when not given. */
  detail?: string;
}) {
  const hatch = useId();
  const size = 120;
  const stroke = 16;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const visible = shares.filter((share) => pick(share) > 0);
  // Each slice starts where the ones before it end.
  const drawn = visible.map((share, index) => ({
    share,
    length: (pick(share) / (total || 1)) * circumference,
    start: visible.slice(0, index).reduce((sum, earlier) => sum + (pick(earlier) / (total || 1)) * circumference, 0),
  }));
  // A core part lights up the core slice.
  const focusKey = hovered.startsWith('core-') ? 'core' : hovered;
  const focus = visible.find((share) => share.key === focusKey);
  return (
    <figure className="donut">
      <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label}: ${format(used)} of ${format(total)} used`}>
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
                className={`donut-slice${share.to ? ' donut-link' : ''}${focusKey && focusKey !== share.key ? ' dim' : ''}`}
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
                <title>{`${share.label}: ${format(pick(share))}`}</title>
              </circle>
            );
          })}
        </g>
      </svg>
      <div className="donut-centre" aria-hidden="true">
        <b>{format(focus ? pick(focus) : used)}</b>
        <small>{focus ? focus.label : detail || `of ${format(total)}`}</small>
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

const shown = (value: number) => (value ? bytes(value) : '—');

function LegendRow({
  share,
  hovered,
  setHovered,
  detailed,
  sub = false,
  open,
  toggle,
  gpu = false,
}: {
  share: Share;
  hovered: string;
  setHovered: (key: string) => void;
  detailed: boolean;
  sub?: boolean;
  open?: boolean;
  toggle?: () => void;
  /** The server has a GPU, so the list has a graphics memory column. */
  gpu?: boolean;
}) {
  const split = share.program !== undefined && share.disk > 0;
  return (
    <tr
      className={`${hovered === share.key ? 'active' : ''}${sub ? ' legend-part' : ''}`}
      onMouseEnter={() => setHovered(share.key)}
      onMouseLeave={() => setHovered('')}
    >
      <th scope="row">
        {!sub && (
          <span
            className={`legend-swatch${share.hatched ? ' hatched' : ''}`}
            style={{ background: share.hatched ? undefined : share.color }}
            aria-hidden="true"
          />
        )}
        {toggle ? (
          <button type="button" className="legend-toggle" aria-expanded={open} onClick={toggle}>
            {share.label}
            {open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
          </button>
        ) : share.to ? (
          <Link to={share.to}>{share.label}</Link>
        ) : (
          share.label
        )}
      </th>
      <td>{shown(share.ram)}</td>
      <td title={split ? `Data ${bytes(share.data || 0)} · program ${bytes(share.program || 0)}` : undefined}>
        {shown(share.disk)}
        {detailed && split && (
          <small className="legend-split">
            {bytes(share.data || 0)} data · {bytes(share.program || 0)} program
          </small>
        )}
      </td>
      {gpu && <td>{shown(share.gpu || 0)}</td>}
    </tr>
  );
}

/**
 * Memory, disk, CPU and (with an NVIDIA GPU) graphics memory as donuts with one list of every app beside them, sortable by memory or
 * disk (Home's System chip, and Settings > System with each app's data and program split out).
 */
export function ResourceCharts({ detailed = false }: { detailed?: boolean }) {
  const { data } = useDashboard();
  const isAdmin = useIsAdmin();
  const [hovered, setHovered] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; descending: boolean }>({ key: 'ram', descending: true });
  const [coreOpen, setCoreOpen] = useState(false);
  const { system } = data;
  const hasGpu = Boolean(system.gpu);
  const { apps, last, freeable } = buildShares(system, data.services.services);
  const value = (share: Share) => share[sort.key] || 0;
  const order = (a: Share, b: Share) => (sort.descending ? value(b) - value(a) : value(a) - value(b));
  const listed = [...apps].sort(order);
  const shares = [...apps, ...last];
  const measured = system.disk_usage?.measured_at;
  const cpu = Math.round(system.cpu_percent);
  // Cleanup waits for running tasks; say so rather than offer a button that is turned away.
  const busy = data.jobs.jobs.some((job) => jobActive(job.state));
  const sortBy = (key: SortKey) =>
    setSort((current) => ({ key, descending: current.key === key ? !current.descending : true }));
  const header = (key: SortKey, label: string) => {
    const Arrow = sort.descending ? ArrowDown : ArrowUp;
    const active = sort.key === key;
    return (
      <th scope="col" aria-sort={active ? (sort.descending ? 'descending' : 'ascending') : 'none'}>
        <button type="button" className={`legend-sort${active ? ' active' : ''}`} onClick={() => sortBy(key)}>
          {label}
          {active && <Arrow aria-hidden="true" />}
        </button>
      </th>
    );
  };
  return (
    <div className="resources-frame">
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
          <Donut
            label="CPU"
            total={100}
            used={cpu}
            shares={[{ key: 'cpu', label: 'CPU', color: 'var(--series-1)', ram: cpu, disk: 0 }]}
            pick={(share) => share.ram}
            hovered=""
            setHovered={() => undefined}
            format={(value) => `${Math.round(value)}%`}
            detail="in use"
          />
          {system.gpu && (
            <Donut
              label="Graphics memory"
              total={system.gpu.total}
              used={system.gpu.used}
              shares={shares}
              pick={(share) => share.gpu || 0}
              hovered={hovered}
              setHovered={setHovered}
            />
          )}
        </div>
        <div className="resources-list">
          <table className="resource-legend">
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">App</span>
                </th>
                {header('ram', 'Memory')}
                {header('disk', 'Disk')}
                {hasGpu && header('gpu', 'Graphics')}
              </tr>
            </thead>
            <tbody>
              {listed.map((share) =>
                share.key === 'core' && share.parts?.length ? (
                  [
                    <LegendRow
                      key={share.key}
                      share={share}
                      hovered={hovered}
                      setHovered={setHovered}
                      detailed={detailed}
                      gpu={hasGpu}
                      open={coreOpen}
                      toggle={() => setCoreOpen(!coreOpen)}
                    />,
                    ...(coreOpen
                      ? [...share.parts]
                          .sort(order)
                          .map((part) => (
                            <LegendRow
                              key={part.key}
                              share={part}
                              hovered={hovered}
                              setHovered={setHovered}
                              detailed={detailed}
                              gpu={hasGpu}
                              sub
                            />
                          ))
                      : []),
                  ]
                ) : (
                  <LegendRow
                    key={share.key}
                    share={share}
                    hovered={hovered}
                    setHovered={setHovered}
                    detailed={detailed}
                    gpu={hasGpu}
                  />
                ),
              )}
              {last.map((share) => (
                <LegendRow
                  key={share.key}
                  share={share}
                  hovered={hovered}
                  setHovered={setHovered}
                  detailed={detailed}
                  gpu={hasGpu}
                />
              ))}
              <tr className="legend-free">
                <th scope="row">
                  <span className="legend-swatch track" aria-hidden="true" />
                  Free
                </th>
                <td>{bytes(Math.max(0, system.memory.total - system.memory.used))}</td>
                <td>{bytes(Math.max(0, system.disk.total - system.disk.used))}</td>
                {system.gpu && <td>{bytes(Math.max(0, system.gpu.total - system.gpu.used))}</td>}
              </tr>
            </tbody>
          </table>
          {isAdmin && freeable >= MIN_FREEABLE && (
            <div className="resources-free">
              <span>
                <b>{bytes(freeable)}</b> can be freed: old app versions and apps Mu3Lab no longer offers.
              </span>
              {busy ? (
                <span className="resources-free-wait">Available once the running task finishes.</span>
              ) : (
                <FreeUpSpace freeable={freeable} folders={system.disk_usage?.removed_folders || []} />
              )}
            </div>
          )}
          <p className="resources-caption">
            {measured
              ? `Memory${hasGpu ? ', graphics memory' : ''} and CPU are live. Disk was measured ${relativeTime(measured)}.`
              : 'Disk use per app is being measured; this takes a few minutes after setup.'}
            {system.uptime_seconds !== undefined && system.uptime_seconds !== null
              ? ` Up ${duration(system.uptime_seconds)}.`
              : ''}
          </p>
        </div>
      </div>
    </div>
  );
}
