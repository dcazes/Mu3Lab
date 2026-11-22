import { ArrowLeft, Check, GripVertical, MoveDiagonal2, Plus, RotateCcw, Settings2, Trash2, X } from 'lucide-react';
import {
  Component,
  type CSSProperties,
  type ErrorInfo,
  type KeyboardEvent,
  type ReactNode,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import type { HomeWidget } from '../../../api';
import { Button } from '../../../components/Button';
import { EmptyState } from '../../../components/Layout';
import { Link } from '../../../lib/router';
import { requestSidebarRoom, resetSidebarRoom, subscribeRoom } from '../../../shell/Sidebar';
import type { Board as BoardState } from './useBoard';
import { WIDGETS } from './widgets';

// Kinds one board needs only once; the rest (two places' weather, two watchlists) can repeat.
const ONCE = new Set(['apps', 'ask']);

/** One widget failing (bad data from outside, a bug) blanks that widget, never the whole Home page. */
class WidgetBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Home widget failed', error, info.componentStack);
  }

  render() {
    return this.state.failed ? (
      <p className="widget-error">This widget hit a problem. Reload the page to try again.</p>
    ) : (
      this.props.children
    );
  }
}

interface Grid {
  columns: number;
  column: number;
  row: number;
}

function measure(element: HTMLElement | null): Grid | null {
  if (!element) return null;
  const style = getComputedStyle(element);
  const columns = style.gridTemplateColumns.split(' ').filter(Boolean).length || 1;
  const gap = parseFloat(style.columnGap) || 0;
  const rowGap = parseFloat(style.rowGap) || 0;
  const width = (element.clientWidth - gap * (columns - 1)) / columns;
  return { columns, column: width + gap, row: (parseFloat(style.gridAutoRows) || 148) + rowGap };
}

const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));

/** Drag a widget by its grip to reorder, and its corner to resize; arrow keys do the same. */
function useArrange(board: BoardState, grid: React.RefObject<HTMLDivElement | null>) {
  const [dragging, setDragging] = useState<string | null>(null);
  const lastTarget = useRef<string | null>(null);
  const { move, update, kindOf } = board;

  useEffect(() => {
    if (!dragging) return;
    const onMove = (event: PointerEvent) => {
      const over = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-widget-id]');
      const id = over?.dataset.widgetId;
      // Moving onto the widget just swapped with must not swap it straight back.
      if (!over || !id || id === dragging) {
        if (id === dragging) lastTarget.current = null;
        return;
      }
      if (id === lastTarget.current) return;
      lastTarget.current = id;
      move(dragging, Number(over.dataset.index));
    };
    const onUp = () => setDragging(null);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [dragging, move]);

  const startDrag = (id: string) => (event: ReactPointerEvent) => {
    if (event.button !== 0) return;
    event.preventDefault();
    lastTarget.current = null;
    setDragging(id);
  };

  const startResize = (widget: HomeWidget) => (event: ReactPointerEvent) => {
    if (event.button !== 0) return;
    const metrics = measure(grid.current);
    const kind = kindOf(widget.kind);
    if (!metrics || !kind) return;
    event.preventDefault();
    const origin = { x: event.clientX, y: event.clientY, w: Math.min(widget.w, metrics.columns), h: widget.h };
    const maxW = Math.min(kind.max_w, metrics.columns);
    let last = `${widget.w}x${widget.h}`;
    const onMove = (moved: PointerEvent) => {
      const w = clamp(origin.w + Math.round((moved.clientX - origin.x) / metrics.column), kind.min_w, maxW);
      const h = clamp(origin.h + Math.round((moved.clientY - origin.y) / metrics.row), kind.min_h, kind.max_h);
      if (`${w}x${h}` === last) return;
      last = `${w}x${h}`;
      update(widget.id, { w, h });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  };

  const dragKeys = (id: string, index: number) => (event: KeyboardEvent) => {
    const step = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 }[event.key];
    if (!step) return;
    event.preventDefault();
    move(id, index + step);
  };

  const resizeKeys = (widget: HomeWidget) => (event: KeyboardEvent) => {
    const kind = kindOf(widget.kind);
    const change = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (!kind || !change) return;
    event.preventDefault();
    update(widget.id, {
      w: clamp(widget.w + change[0], kind.min_w, kind.max_w),
      h: clamp(widget.h + change[1], kind.min_h, kind.max_h),
    });
  };

  return { dragging, startDrag, startResize, dragKeys, resizeKeys };
}

function WidgetCard({
  widget,
  index,
  board,
  arrange,
  current,
  onPick,
  onConfigure,
}: {
  widget: HomeWidget;
  index: number;
  board: BoardState;
  arrange: ReturnType<typeof useArrange>;
  current: boolean;
  onPick: (id: string) => void;
  onConfigure: (id: string) => void;
}) {
  const info = WIDGETS[widget.kind];
  const { editing } = board;
  const Icon = info.icon;
  const classes = [
    'widget',
    editing && 'is-editing',
    arrange.dragging === widget.id && 'is-dragging',
    current && 'is-current',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <article
      className={classes}
      data-widget-id={widget.id}
      data-index={index}
      data-w={widget.w}
      data-h={widget.h}
      style={{ '--w': widget.w, '--h': widget.h } as CSSProperties}
      aria-label={info.title}
    >
      <header className="widget-head">
        {editing && (
          <button
            type="button"
            className="widget-grip"
            aria-label={`Move ${info.title}`}
            title="Drag to move (or use the arrow keys)"
            onPointerDown={arrange.startDrag(widget.id)}
            onKeyDown={arrange.dragKeys(widget.id, index)}
          >
            <GripVertical />
          </button>
        )}
        <Icon className="widget-icon" aria-hidden />
        <h3>{info.to && !editing ? <Link to={info.to}>{info.title}</Link> : info.title}</h3>
        {editing && info.Settings && (
          <button
            type="button"
            className="widget-tool"
            aria-label={`${info.title} settings`}
            aria-pressed={current}
            onClick={() => onPick(widget.id)}
          >
            <Settings2 />
          </button>
        )}
        {editing && (
          <button
            type="button"
            className="widget-tool"
            aria-label={`Remove ${info.title}`}
            onClick={() => board.remove(widget.id)}
          >
            <X />
          </button>
        )}
      </header>
      <div className="widget-body">
        <WidgetBoundary>
          <info.Component widget={widget} onConfigure={() => onConfigure(widget.id)} />
        </WidgetBoundary>
      </div>
      {editing && (
        <button
          type="button"
          className="widget-resize"
          aria-label={`Resize ${info.title}: ${widget.w} wide, ${widget.h} tall`}
          title="Drag to resize (or use the arrow keys)"
          onPointerDown={arrange.startResize(widget)}
          onKeyDown={arrange.resizeKeys(widget)}
        >
          <MoveDiagonal2 />
        </button>
      )}
    </article>
  );
}

/** Edit mode's side panel: the widgets to add, or the picked widget's settings. */
function EditPanel({
  board,
  picked,
  onPick,
  onClose,
  finish,
}: {
  board: BoardState;
  picked: HomeWidget | undefined;
  onPick: (id: string | null) => void;
  onClose: () => void;
  /** Cancel and Done in the panel itself, when it covers the page's own (a narrow window). */
  finish?: { cancel: () => void; done: () => void };
}) {
  const footer = finish && (
    <div className="board-panel-finish">
      <Button variant="ghost" size="sm" onClick={finish.cancel}>
        Cancel
      </Button>
      <Button variant="primary" size="sm" icon={Check} loading={board.saving} onClick={finish.done}>
        Done
      </Button>
    </div>
  );
  if (picked) {
    const info = WIDGETS[picked.kind];
    const Settings = info.Settings;
    return (
      <aside className="details-card board-panel" aria-label={`${info.title} settings`}>
        <header className="board-panel-head">
          <Button
            variant="ghost"
            size="sm"
            icon={ArrowLeft}
            className="btn-icon"
            aria-label="All widgets"
            onClick={() => onPick(null)}
          />
          <h3>{info.title}</h3>
          <Button variant="ghost" size="sm" icon={X} className="btn-icon" aria-label="Close" onClick={onClose} />
        </header>
        {Settings ? (
          <Settings
            widget={picked}
            onChange={(settings) => board.update(picked.id, { settings: settings as HomeWidget['settings'] })}
          />
        ) : (
          <p className="muted">This widget has nothing to set.</p>
        )}
        <Button
          variant="ghost"
          size="sm"
          icon={Trash2}
          className="board-panel-remove"
          onClick={() => board.remove(picked.id)}
        >
          Remove from board
        </Button>
        {footer}
      </aside>
    );
  }
  const present = new Set(board.widgets.map((widget) => widget.kind));
  return (
    <aside className="details-card board-panel" aria-label="Add a widget">
      <header className="board-panel-head">
        <h3>Add a widget</h3>
        <Button variant="ghost" size="sm" icon={X} className="btn-icon" aria-label="Close" onClick={onClose} />
      </header>
      <ul className="board-kinds">
        {board.kinds.map((kind) => {
          const info = WIDGETS[kind.kind];
          const Icon = info.icon;
          const added = ONCE.has(kind.kind) && present.has(kind.kind);
          return (
            <li key={kind.kind}>
              <Icon className="board-kind-icon" aria-hidden />
              <div>
                <strong>{info.title}</strong>
                <p>{info.description}</p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                icon={added ? undefined : Plus}
                disabled={added}
                aria-label={added ? `${info.title} is on your board` : `Add ${info.title}`}
                onClick={() => {
                  const id = board.add(kind);
                  if (info.Settings) onPick(id);
                }}
              >
                {added ? 'Added' : 'Add'}
              </Button>
            </li>
          );
        })}
      </ul>
      <Button
        variant="ghost"
        size="sm"
        icon={RotateCcw}
        className="board-panel-reset"
        onClick={() => void board.reset()}
      >
        Start over with the starter board
      </Button>
      {footer}
    </aside>
  );
}

// The edit panel's width, the gap beside the board, and the margin it keeps from the window's edge.
const PANEL = 280 + 20 + 16;

/**
 * Room for the edit panel without changing the board: the page slides left into its own margin by
 * as much as the panel needs (and the margin allows). The board keeps its width and columns, so
 * what you arrange is what Home shows after Done. Phones use the bottom sheet instead.
 */
export function usePanelRoom(open: boolean) {
  const home = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState({ shift: 0, covered: false });
  useEffect(() => {
    const element = home.current;
    const board = element?.querySelector<HTMLElement>('.board');
    const inner = element?.parentElement;
    if (!open || !element || !board || !inner) return;
    resetSidebarRoom();
    // The board keeps the width it has now, even if the sidebar folds and the page gets wider.
    board.style.width = `${board.offsetWidth}px`;
    // Where the board will sit, worked out from the layout's own sizes, so nothing waits for the
    // sidebar to finish folding: the board and the panel move together, once.
    const measure = () => {
      const width = document.documentElement.clientWidth;
      if (width <= 1000) {
        setMeasured({ shift: 0, covered: false });
        return;
      }
      const root = getComputedStyle(document.documentElement);
      const full = parseFloat(root.getPropertyValue('--sidebar-width')) || 240;
      const folded = parseFloat(root.getPropertyValue('--sidebar-collapsed-width')) || 56;
      const page = getComputedStyle(inner);
      const maxWidth = parseFloat(page.maxWidth) || Infinity;
      const padding = parseFloat(page.paddingLeft) || 0;
      const place = (sidebar: number) => {
        const main = width - sidebar;
        const left = sidebar + (main - Math.min(maxWidth, main)) / 2 + padding;
        const needed = left + board.offsetWidth + PANEL - width;
        const room = left - sidebar - 16;
        return { needed, room };
      };
      const sidebarNow = document.querySelector('.shell.sidebar-collapsed') ? folded : full;
      let { needed, room } = place(sidebarNow);
      // Not enough margin: borrow the sidebar's width too (it folds to icons until the panel closes).
      if (needed > room && sidebarNow === full && requestSidebarRoom(true)) ({ needed, room } = place(folded));
      setMeasured({ shift: Math.max(0, Math.min(needed, room)), covered: needed > room });
    };
    let timer = 0;
    // After the sidebar changes, once React has drawn it; never waiting on animation frames, which a
    // background tab holds back.
    const later = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(measure, 0);
    };
    measure();
    // The person opening the sidebar again moves the page: place it again.
    const unsubscribe = subscribeRoom(later);
    window.addEventListener('resize', later);
    return () => {
      window.clearTimeout(timer);
      unsubscribe();
      window.removeEventListener('resize', later);
      requestSidebarRoom(false);
      board.style.width = '';
    };
  }, [open]);
  return { home, shift: open ? measured.shift : 0, covered: open && measured.covered };
}

/** What edit mode's side panel shows: nothing, the widgets to add, or one widget's settings (its id). */
export type Panel = 'closed' | 'add' | string;

/** The person's Home board: widgets in their order, and Edit mode to change it. */
export function BoardView({
  board,
  panel,
  setPanel,
  finish,
}: {
  board: BoardState;
  panel: Panel;
  setPanel: (panel: Panel) => void;
  finish?: { cancel: () => void; done: () => void };
}) {
  const grid = useRef<HTMLDivElement>(null);
  const arrange = useArrange(board, grid);
  const picked = board.editing ? board.widgets.find((widget) => widget.id === panel) : undefined;
  const pick = useCallback((id: string | null) => setPanel(id ?? 'add'), [setPanel]);
  const { editing, startEditing } = board;
  const configure = useCallback(
    (id: string) => {
      if (!editing) startEditing();
      setPanel(id);
    },
    [editing, startEditing, setPanel],
  );
  if (!board.loaded)
    return board.error ? (
      <EmptyState title="Your board could not be loaded">{board.error}</EmptyState>
    ) : (
      <p className="muted">Loading your board…</p>
    );
  const showPanel = board.editing && panel !== 'closed';
  return (
    <div className={`board-layout${showPanel ? ' has-panel' : ''}`}>
      {board.widgets.length ? (
        <div ref={grid} className={`board${board.editing ? ' is-editing' : ''}`}>
          {board.widgets.map((widget, index) => (
            <WidgetCard
              key={widget.id}
              widget={widget}
              index={index}
              board={board}
              arrange={arrange}
              current={widget.id === picked?.id}
              onPick={pick}
              onConfigure={configure}
            />
          ))}
        </div>
      ) : (
        <EmptyState title="Your board is empty">
          {board.editing ? 'Add a widget from the list.' : 'Choose Edit to add weather, your calendar and more.'}
        </EmptyState>
      )}
      {/* In the body, not the page: the page slides to make room, and a slid page would carry the panel along. */}
      {showPanel &&
        createPortal(
          <EditPanel board={board} picked={picked} onPick={pick} onClose={() => setPanel('closed')} finish={finish} />,
          document.body,
        )}
    </div>
  );
}
