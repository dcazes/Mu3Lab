import { Link } from '../../../lib/router';
import { bytes } from '../../../lib/format';
import { useDashboard } from '../../../state/dashboard';
import { FitList } from './size';
import type { WidgetProps } from './widgets';

interface Meter {
  key: string;
  label: string;
  percent: number;
  detail: string;
}

const tone = (percent: number) => (percent >= 90 ? 'bad' : percent >= 75 ? 'warn' : 'good');

/**
 * This server's processor, memory, graphics memory and drives, as bars. It shows the figures the
 * dashboard already keeps up to date, so it adds no checks of its own.
 */
export function ResourcesWidget(_props: WidgetProps) {
  const { system } = useDashboard().data;
  if (!system.ok) return <p className="muted">Loading…</p>;
  const meters: Meter[] = [
    { key: 'cpu', label: 'Processor', percent: system.cpu_percent, detail: `${Math.round(system.cpu_percent)}%` },
    {
      key: 'ram',
      label: 'Memory',
      percent: system.memory.percent,
      detail: `${bytes(system.memory.used)} of ${bytes(system.memory.total)}`,
    },
  ];
  if (system.gpu && system.gpu.total > 0)
    meters.push({
      key: 'gpu',
      label: 'Graphics memory',
      percent: (system.gpu.used / system.gpu.total) * 100,
      detail: `${bytes(system.gpu.used)} of ${bytes(system.gpu.total)}`,
    });
  meters.push({
    key: 'disk',
    label: 'Main drive',
    percent: system.disk.percent,
    detail: `${bytes(system.disk.total - system.disk.used)} free`,
  });
  for (const drive of system.drives ?? [])
    meters.push({
      key: `drive-${drive.mount}`,
      label: drive.name || drive.mount,
      percent: drive.percent,
      detail: `${bytes(drive.total - drive.used)} free`,
    });
  return (
    <FitList
      className="resource-meters"
      label="Server resources"
      more={(hidden) => <Link to="/settings/system">+{hidden} more</Link>}
    >
      {meters.map((meter) => (
        <li key={meter.key}>
          <span className="resource-label">{meter.label}</span>
          <span className="resource-detail muted">{meter.detail}</span>
          <span
            className={`resource-bar tone-${tone(meter.percent)}`}
            role="meter"
            aria-label={meter.label}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(meter.percent)}
          >
            <i style={{ width: `${Math.min(100, Math.max(0, meter.percent))}%` }} />
          </span>
        </li>
      ))}
    </FitList>
  );
}
