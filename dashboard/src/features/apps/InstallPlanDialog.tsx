import { AlertTriangle, ChevronDown, ChevronUp, GripVertical } from 'lucide-react';
import { useState } from 'react';
import type { AppSize, AppSizesResponse } from '../../api';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Callout } from '../../components/Layout';
import { bytes } from '../../lib/format';

const PARALLEL_KEY = 'mu3lab.parallelDownloads';
const DEFAULT_PARALLEL = 3;
const MAX_PARALLEL = 8;

export function savedParallel() {
  try {
    const value = Number(localStorage.getItem(PARALLEL_KEY));
    return value >= 1 && value <= MAX_PARALLEL ? value : DEFAULT_PARALLEL;
  } catch {
    return DEFAULT_PARALLEL;
  }
}

/** "2.2 GB download · about 8.3 GB on disk", or what is already here. */
export function sizeLabel(size?: AppSize) {
  if (!size) return 'Checking size…';
  const disk = `about ${bytes(size.disk_bytes)} on disk`;
  if (!size.needed_bytes) return `Already downloaded · ${disk}`;
  if (size.needed_bytes < size.download_bytes) return `${bytes(size.needed_bytes)} left to download · ${disk}`;
  return `${bytes(size.download_bytes)} download · ${disk}`;
}

/** Smallest download first, so small apps are ready soonest; unmeasured apps go last. */
export function smallestFirst(ids: string[], sizes: AppSizesResponse | null) {
  const needed = (id: string) => sizes?.apps[id]?.needed_bytes ?? Number.MAX_SAFE_INTEGER;
  return [...ids].sort((a, b) => needed(a) - needed(b));
}

export function InstallPlanDialog({
  ids,
  names,
  sizes,
  pending,
  onClose,
  onInstall,
}: {
  ids: string[];
  names: Record<string, string>;
  sizes: AppSizesResponse | null;
  pending: boolean;
  onClose: () => void;
  onInstall: (order: string[], parallel: number) => void;
}) {
  // Smallest first follows the sizes as they arrive, until the owner reorders by hand.
  const [manual, setManual] = useState<string[] | null>(null);
  const order = manual ?? smallestFirst(ids, sizes);
  const [parallel, setParallel] = useState(savedParallel);
  const [dragging, setDragging] = useState('');
  const selection = sizes?.selection;
  const tooBig = !!selection && !!sizes?.free_bytes && selection.needed_disk_bytes > sizes.free_bytes;
  const move = (id: string, target: number) => {
    const next = order.filter((item) => item !== id);
    next.splice(Math.max(0, Math.min(target, next.length)), 0, id);
    setManual(next);
  };
  const install = () => {
    try {
      localStorage.setItem(PARALLEL_KEY, String(parallel));
    } catch {
      /* the choice still applies to this install */
    }
    onInstall(order, parallel);
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Install ${ids.length} app${ids.length === 1 ? '' : 's'}`}
      description="Apps download together and each is set up as soon as its download finishes, so the ones at the top are ready first. Drag to change the order. Anything an app depends on is added automatically."
      size="lg"
    >
      <ol className="install-plan">
        {order.map((id, index) => (
          <li
            key={id}
            draggable
            className={dragging === id ? 'is-dragging' : ''}
            onDragStart={(event) => {
              setDragging(id);
              event.dataTransfer.effectAllowed = 'move';
            }}
            onDragEnd={() => setDragging('')}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              if (dragging && dragging !== id) move(dragging, index);
              setDragging('');
            }}
          >
            <GripVertical className="install-grip" aria-hidden />
            <span className="install-plan-name">
              <b>{names[id] || id}</b>
              <small>{sizeLabel(sizes?.apps[id])}</small>
            </span>
            <Button
              size="sm"
              variant="ghost"
              icon={ChevronUp}
              aria-label={`Move ${names[id] || id} up`}
              disabled={index === 0}
              onClick={() => move(id, index - 1)}
            />
            <Button
              size="sm"
              variant="ghost"
              icon={ChevronDown}
              aria-label={`Move ${names[id] || id} down`}
              disabled={index === order.length - 1}
              onClick={() => move(id, index + 1)}
            />
          </li>
        ))}
      </ol>
      <div className="install-plan-summary">
        <p>
          {selection && sizes?.measured
            ? `${bytes(selection.needed_bytes)} to download · about ${bytes(selection.needed_disk_bytes)} of disk space · ${bytes(sizes.free_bytes)} free`
            : 'Checking download sizes…'}
        </p>
        <label className="install-parallel">
          <span>Downloads at once</span>
          <select value={parallel} onChange={(event) => setParallel(Number(event.target.value))}>
            {Array.from({ length: MAX_PARALLEL }, (_, index) => index + 1).map((count) => (
              <option key={count} value={count}>
                {count}
              </option>
            ))}
          </select>
        </label>
      </div>
      {tooBig && (
        <Callout tone="danger" icon={AlertTriangle} title="Not enough free disk space">
          These apps need about {bytes(selection.needed_disk_bytes)}, but only {bytes(sizes.free_bytes)} is free. Remove
          an app from the selection or free up space first.
        </Callout>
      )}
      <footer className="form-footer">
        <span className="spacer" />
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" loading={pending} disabled={tooBig} onClick={install}>
          Install all selected
        </Button>
      </footer>
    </Dialog>
  );
}
