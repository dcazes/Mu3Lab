import { ChevronDown } from 'lucide-react';
import { useState } from 'react';
import { Dot } from '../../components/Status';
import { bytes } from '../../lib/format';
import type { Tone } from '../../lib/services';
import { useDashboard } from '../../state/dashboard';
import { ResourceCharts } from './ResourceCharts';

/** Amber, then red, as a figure passes its warning and critical levels. */
const toneFor = (percent: number, warn: number, critical: number): Tone =>
  percent >= critical ? 'red' : percent >= warn ? 'amber' : 'green';
const RANK: Record<Tone, number> = { green: 0, gray: 0, blue: 1, amber: 2, red: 3 };

/** One System chip with memory, disk, CPU and (with an NVIDIA GPU) graphics memory; it opens the donuts and every app's share. */
export function SystemStatus() {
  const { data } = useDashboard();
  const [open, setOpen] = useState(false);
  const { system } = data;
  if (!system.ok) return null;
  const ram = Math.round(system.memory.percent);
  const disk = Math.round(system.disk.percent);
  const cpu = Math.round(system.cpu_percent);
  // Graphics memory is not in the warning colour: a loaded model fills it on purpose.
  const gpu = system.gpu ? Math.round((system.gpu.used / (system.gpu.total || 1)) * 100) : null;
  const tone = [toneFor(ram, 90, 97), toneFor(disk, 85, 95), toneFor(cpu, 95, 101)].reduce((worst, current) =>
    RANK[current] > RANK[worst] ? current : worst,
  );
  return (
    <section className="status-strip" aria-label="System status">
      <div className="status-chips">
        <button
          type="button"
          className="status-chip"
          aria-expanded={open}
          aria-controls="status-panel-system"
          title={[
            `Memory ${bytes(system.memory.used)} of ${bytes(system.memory.total)}`,
            `Disk ${bytes(system.disk.used)} of ${bytes(system.disk.total)}`,
            system.gpu ? `Graphics memory ${bytes(system.gpu.used)} of ${bytes(system.gpu.total)}` : '',
          ]
            .filter(Boolean)
            .join(' · ')}
          onClick={() => setOpen(!open)}
        >
          <Dot tone={tone} />
          <span className="status-chip-name">System</span>
          <span className="status-chip-summary">
            RAM {ram}% · Disk {disk}% · CPU {cpu}%{gpu !== null && ` · GPU ${gpu}%`}
          </span>
          <ChevronDown aria-hidden="true" />
        </button>
      </div>
      {open && (
        <div className="status-panel-system" id="status-panel-system">
          <ResourceCharts />
        </div>
      )}
    </section>
  );
}
