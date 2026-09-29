import { ChevronDown } from 'lucide-react';
import { useState } from 'react';
import type { Service, SystemResponse } from '../../api';
import { Dot } from '../../components/Status';
import { bytes, duration } from '../../lib/format';
import { Link } from '../../lib/router';
import { stateLabel, stateTone, type Tone } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';

interface Row {
  key: string;
  label: string;
  tone: Tone;
  status: string;
  /** Right-aligned figure, e.g. "3.2 GB" or "9.8 GB of 16 GB". */
  value?: string;
  /** 0-100; draws a usage bar under the row. */
  percent?: number;
  detail?: string;
  to: string;
}

interface Group {
  key: 'security' | 'ai' | 'system';
  label: string;
  rows: Row[];
  /** What the collapsed chip says next to the name. */
  summary: string;
  /** Hover text on the collapsed chip. */
  hint: string;
}

const SECURITY = ['ingress', 'authentik', 'tailscale', 'vaultwarden'];
const AI = ['ollama', 'litellm', 'freellmapi', 'firecrawl'];
const SYSTEM_PAGE = '/settings/system';
const RANK: Record<Tone, number> = { green: 0, gray: 0, blue: 1, amber: 2, red: 3 };

function serviceRow(id: string, services: Service[], memory: Record<string, number>): Row | null {
  const service = services.find((candidate) => candidate.id === id);
  if (!service) return null;
  const tone = stateTone(service.state);
  const used = (service.containers || []).reduce((sum, container) => sum + (memory[container.name] || 0), 0);
  return {
    key: id,
    label: service.name,
    tone,
    status: stateLabel[service.state] || service.state,
    value: used ? bytes(used) : undefined,
    detail: tone === 'green' ? '' : service.detail,
    to: `/apps/${id}`,
  };
}

function tailscaleRow(system: SystemResponse): Row {
  const state = system.tailscale?.state;
  return {
    key: 'tailscale',
    label: 'Tailscale',
    tone: state === 'connected' ? 'green' : state === 'disconnected' ? 'red' : 'gray',
    status: state === 'connected' ? 'Connected' : state === 'disconnected' ? 'Disconnected' : 'Unknown',
    detail: system.tailscale?.detail,
    to: SYSTEM_PAGE,
  };
}

function usageRow(key: string, label: string, metric: SystemResponse['memory'], warn: number, critical: number): Row {
  const percent = Math.round(metric.percent);
  return {
    key,
    label,
    tone: percent >= critical ? 'red' : percent >= warn ? 'amber' : 'green',
    status: `${percent}%`,
    value: metric.total ? `${bytes(metric.used)} of ${bytes(metric.total)}` : undefined,
    percent,
    to: SYSTEM_PAGE,
  };
}

function worstTone(rows: Row[]): Tone {
  const worst = rows.reduce<Tone>((current, row) => (RANK[row.tone] > RANK[current] ? row.tone : current), 'gray');
  // Nothing broken and nothing running (e.g. no AI services installed) is neutral, not healthy.
  return worst === 'gray' && rows.some((row) => row.tone === 'green') ? 'green' : worst;
}

function healthSummary(rows: Row[]): string {
  const total = rows.length;
  const healthy = rows.filter((row) => row.tone === 'green').length;
  const broken = rows.filter((row) => row.tone === 'red' || row.tone === 'amber').length;
  if (broken) return `${broken} need${broken === 1 ? 's' : ''} attention`;
  return healthy === total ? `${total}/${total} healthy` : `${healthy}/${total} running`;
}

function buildGroups(system: SystemResponse, services: Service[]): Group[] {
  const memory = system.container_memory || {};
  const pick = (ids: string[]) =>
    ids
      .map((id) => (id === 'tailscale' ? tailscaleRow(system) : serviceRow(id, services, memory)))
      .filter((row): row is Row => row !== null);
  const worker = system.worker_state || 'unknown';
  const workerTone: Tone =
    worker === 'active' ? 'green' : worker === 'activating' ? 'blue' : worker === 'unknown' ? 'gray' : 'red';
  const ram = usageRow('memory', 'Memory', system.memory, 90, 97);
  const disk = usageRow('disk', 'Disk', system.disk, 85, 95);
  const cpu = Math.round(system.cpu_percent);
  const systemRows: Row[] = [
    ram,
    disk,
    {
      key: 'cpu',
      label: 'CPU',
      tone: cpu >= 95 ? 'amber' : 'green',
      status: `${cpu}%`,
      percent: cpu,
      to: SYSTEM_PAGE,
    },
    {
      key: 'docker',
      label: 'Docker',
      tone: system.docker_ready ? 'green' : 'red',
      status: system.docker_ready ? 'Running' : 'Not reachable',
      to: SYSTEM_PAGE,
    },
    {
      key: 'worker',
      label: 'Background worker',
      tone: workerTone,
      status: worker === 'active' ? 'Running' : worker === 'unknown' ? 'Unknown' : worker,
      detail: workerTone === 'red' ? 'Installs and repairs wait until it runs again.' : '',
      to: SYSTEM_PAGE,
    },
  ];
  const security = pick(SECURITY);
  const ai = pick(AI);
  const figures = (rows: Row[]) => rows.filter((row) => row.value).map((row) => `${row.label} ${row.value}`);
  return [
    {
      key: 'security',
      label: 'Security',
      rows: security,
      summary: healthSummary(security),
      hint: figures(security).join(' · '),
    },
    { key: 'ai', label: 'AI', rows: ai, summary: healthSummary(ai), hint: figures(ai).join(' · ') },
    {
      key: 'system',
      label: 'System',
      rows: systemRows,
      summary: `RAM ${ram.status} · Disk ${disk.status}`,
      hint: figures([ram, disk]).join(' · '),
    },
  ];
}

function DetailRow({ row }: { row: Row }) {
  const title = [row.value, row.detail].filter(Boolean).join(' — ');
  return (
    <li>
      <Link to={row.to} className="status-row" title={title || undefined} aria-label={`${row.label}: ${row.status}`}>
        <Dot tone={row.tone} />
        <span className="status-row-name">{row.label}</span>
        <span className="status-row-state">
          {row.status}
          {row.value && <span className="status-row-value">{row.value}</span>}
        </span>
        {row.percent !== undefined && (
          <span className="status-bar" aria-hidden="true">
            <span className={`status-bar-fill tone-${row.tone}`} style={{ width: `${Math.min(100, row.percent)}%` }} />
          </span>
        )}
      </Link>
    </li>
  );
}

/** Security, AI and System health as three chips; each opens its own detail list. */
export function SystemStatus() {
  const { data } = useDashboard();
  const [open, setOpen] = useState<string | null>(null);
  const { system } = data;
  if (!system.ok) return null;
  const groups = buildGroups(system, data.services.services);
  const expanded = groups.find((group) => group.key === open);
  return (
    <section className="status-strip" aria-label="System status">
      <div className="status-chips">
        {groups.map((group) => {
          const isOpen = group.key === open;
          return (
            <button
              key={group.key}
              type="button"
              className="status-chip"
              aria-expanded={isOpen}
              aria-controls={`status-panel-${group.key}`}
              title={group.hint || undefined}
              onClick={() => setOpen(isOpen ? null : group.key)}
            >
              <Dot tone={worstTone(group.rows)} />
              <span className="status-chip-name">{group.label}</span>
              <span className="status-chip-summary">{group.summary}</span>
              <ChevronDown aria-hidden="true" />
            </button>
          );
        })}
        {system.uptime_seconds !== undefined && (
          <span className="status-meta">Up {duration(system.uptime_seconds)}</span>
        )}
      </div>
      {expanded && (
        <ul className="status-panel" id={`status-panel-${expanded.key}`} aria-label={`${expanded.label} details`}>
          {expanded.rows.map((row) => (
            <DetailRow key={row.key} row={row} />
          ))}
        </ul>
      )}
    </section>
  );
}
