import {
  ArrowLeft,
  Check,
  ChevronRight,
  Copy,
  ExternalLink,
  GripVertical,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Settings2,
  Smartphone,
  Trash2,
  X,
} from 'lucide-react';
import type { GridStackNode } from 'gridstack';
import 'gridstack/dist/gridstack.css';
import { GridStack as Grid } from 'gridstack';
import { type ComponentMap, GridStack, type GridStackHandle, type GridStackOptions } from 'gridstack/dist/react';
import {
  Component,
  createContext,
  type CSSProperties,
  type ErrorInfo,
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import type { HomeWidget, HomeWidgetKind } from '../../../api';
import { Button } from '../../../components/Button';
import { EmptyState } from '../../../components/Layout';
import { Menu, type MenuItem } from '../../../components/Menu';
import { CATEGORIES, categoryLabel, launchTarget } from '../../../lib/services';
import { useDashboard } from '../../../state/dashboard';
import { Link } from '../../../lib/router';
import { requestSidebarRoom, resetSidebarRoom, subscribeRoom } from '../../../shell/Sidebar';
import { type Board as BoardState, limitsFor, overlaps, scaled, type Spot } from './useBoard';
import { SwappingEngine } from './gridEngine';
import { refreshWidget } from './AppWidget';
import { BoxContext, useMeasured } from './size';
import { widgetInfo } from './widgets';

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

// One grid row, and the space between widgets; a phone-width board shows one column instead.
const ROW = 60;
const GAP = 16;
const STACKED = 700;

/** Arrow keys on a widget's grip: move it one cell, or with Shift resize it, when nothing is in the way. */
function useKeyboardArrange(board: BoardState) {
  const { widgets, columns, kindOf, update } = board;
  return (widget: HomeWidget) => (event: KeyboardEvent) => {
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    const kind = kindOf(widget.kind);
    if (!step || !kind) return;
    event.preventDefault();
    const limit = limitsFor(kind, columns, board.base);
    const next = event.shiftKey
      ? {
          ...widget,
          w: Math.max(limit.minW, Math.min(limit.maxW, columns - widget.x, widget.w + step[0])),
          h: Math.max(limit.minH, Math.min(limit.maxH, widget.h + step[1])),
        }
      : {
          ...widget,
          x: Math.max(0, Math.min(columns - widget.w, widget.x + step[0])),
          y: Math.max(0, widget.y + step[1]),
        };
    if (widgets.some((other) => other.id !== widget.id && overlaps(other, next))) return;
    update(widget.id, { x: next.x, y: next.y, w: next.w, h: next.h });
  };
}

function WidgetCard({
  widget,
  board,
  current,
  stacked,
  onPick,
  onConfigure,
  onKeys,
}: {
  widget: HomeWidget;
  board: BoardState;
  current: boolean;
  stacked: boolean;
  onPick: (id: string) => void;
  onConfigure: (id: string) => void;
  onKeys: (widget: HomeWidget) => (event: KeyboardEvent) => void;
}) {
  const info = widgetInfo(widget.kind, board.kinds);
  const { editing } = board;
  const Icon = info.icon;
  const [body, box] = useMeasured<HTMLDivElement>();
  const service = useDashboard().data.services.services.find((item) => item.id === info.appId);
  const open = service ? launchTarget(service) : null;
  // Refresh asks the app again and redraws; a new key remounts the widget, so it loads afresh.
  const [round, setRound] = useState(0);
  const menu: MenuItem[] = editing
    ? ONCE.has(widget.kind)
      ? []
      : [{ label: 'Duplicate', icon: Copy, onSelect: () => board.duplicate(widget.id) }]
    : info.appId
      ? [
          {
            label: 'Refresh',
            icon: RefreshCw,
            onSelect: () =>
              void refreshWidget(widget.kind)
                .catch(() => undefined)
                .then(() => setRound((count) => count + 1)),
          },
          ...(open
            ? [
                {
                  label: `Open ${info.appName ?? 'app'}`,
                  icon: ExternalLink,
                  onSelect: () => window.open(open.url, '_blank', 'noopener'),
                },
              ]
            : []),
        ]
      : [];
  const classes = ['widget', editing && 'is-editing', current && 'is-current'].filter(Boolean).join(' ');
  return (
    <article
      className={classes}
      data-widget-id={widget.id}
      // Stacked on a phone, the widget is as tall as its rows would be.
      style={stacked ? ({ height: widget.h * ROW + (widget.h - 1) * GAP } as CSSProperties) : undefined}
      aria-label={info.title}
    >
      {/* In edit mode the whole title bar is the handle that moves the widget. */}
      <header className="widget-head">
        {editing && !stacked && (
          <span
            role="button"
            tabIndex={0}
            className="widget-grip"
            aria-label={`Move ${info.title}`}
            title="Drag the title bar to move. Arrow keys move it; Shift and arrow keys resize it."
            onKeyDown={onKeys(widget)}
          >
            <GripVertical />
          </span>
        )}
        <Icon className="widget-icon" aria-hidden />
        <h3>
          {editing ? (
            info.title
          ) : info.to ? (
            <Link to={info.to}>{info.title}</Link>
          ) : open ? (
            <a href={open.url} target="_blank" rel="noreferrer" title={`Open ${info.appName ?? 'the app'}`}>
              {info.title}
            </a>
          ) : (
            info.title
          )}
        </h3>
        {editing && widget.hide_on_phone && (
          <span className="widget-flag" title="Hidden on phone">
            <Smartphone aria-hidden />
            <span className="sr-only">Hidden on phone</span>
          </span>
        )}
        {editing && (
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
        <Menu items={menu} label={`${info.title} actions`} compact />
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
      <div ref={body} className="widget-body">
        <BoxContext.Provider value={box}>
          <WidgetBoundary>
            <info.Component key={round} widget={widget} onConfigure={() => onConfigure(widget.id)} />
          </WidgetBoundary>
        </BoxContext.Provider>
      </div>
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
    const info = widgetInfo(picked.kind, board.kinds);
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
        {Settings && (
          <Settings
            widget={picked}
            onChange={(settings) => board.update(picked.id, { settings: settings as HomeWidget['settings'] })}
          />
        )}
        <label className="board-panel-switch">
          <input
            type="checkbox"
            checked={picked.hide_on_phone ?? false}
            onChange={(event) => board.update(picked.id, { hide_on_phone: event.target.checked })}
          />
          <span>
            Hide on phone
            <small>A phone shows the board as one column; leave out widgets that are only worth a big screen.</small>
          </span>
        </label>
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
  return <AddList board={board} onPick={onPick} onClose={onClose} footer={footer} />;
}

// Widgets whose preview would do something (the camera starts its stream) show their icon instead.
const NO_PREVIEW = new Set(['camera']);

// The preview's width in the panel, and the board column it is drawn at (a little narrower than a big
// screen's, so it shrinks less and stays readable).
const PREVIEW_WIDTH = 248;
const PREVIEW_COLUMN = 90;
// A widget's frame around its body: padding, and the title above it.
const FRAME_X = 28;
const FRAME_Y = 26 + 32;

/** A widget as it would look when added, shrunk to fit the panel; nothing in it can be clicked. */
function Preview({ kind, board }: { kind: HomeWidgetKind; board: BoardState }) {
  const info = widgetInfo(kind.kind, board.kinds);
  const Icon = info.icon;
  const width = kind.w * PREVIEW_COLUMN - GAP;
  const height = kind.h * (ROW + GAP) - GAP;
  const scale = Math.min(1, PREVIEW_WIDTH / width);
  const sample: HomeWidget = {
    id: `preview-${kind.kind.replace(/[^a-z0-9-]/g, '-')}`,
    kind: kind.kind,
    x: 0,
    y: 0,
    w: scaled(kind.w, board.columns, board.base),
    h: kind.h,
    hide_on_phone: false,
    settings: {},
  };
  return (
    // inert: nothing in a preview can be focused or clicked (set directly; React 18 does not know it).
    <div
      className="board-kind-preview"
      style={{ height: height * scale }}
      aria-hidden
      ref={(element) => element?.setAttribute('inert', '')}
    >
      <div className="widget" style={{ width, height, transform: `scale(${scale})` }}>
        <header className="widget-head">
          <Icon className="widget-icon" aria-hidden />
          <h3>{info.title}</h3>
        </header>
        <div className="widget-body">
          <BoxContext.Provider value={{ width: width - FRAME_X, height: height - FRAME_Y }}>
            <WidgetBoundary>
              <info.Component widget={sample} onConfigure={() => undefined} />
            </WidgetBoundary>
          </BoxContext.Provider>
        </div>
      </div>
    </div>
  );
}

interface KindGroup {
  id: string;
  label: string;
  kinds: HomeWidgetKind[];
}

/**
 * "Add a widget": a short list of names, grouped as the Apps page groups apps (Mu3Lab's own first),
 * with a search box. Choosing a name shows its preview, what it does and Add; any name can also be
 * dragged straight onto the board.
 */
function AddList({
  board,
  onPick,
  onClose,
  footer,
}: {
  board: BoardState;
  onPick: (id: string | null) => void;
  onClose: () => void;
  footer: ReactNode;
}) {
  const [query, setQuery] = useState('');
  // The one name showing its preview, and the groups opened (Mu3Lab's own start open).
  const [chosen, setChosen] = useState<string | null>(null);
  const [opened, setOpened] = useState<ReadonlySet<string>>(() => new Set(['mu3lab']));
  const services = useDashboard().data.services.services;
  const list = useRef<HTMLElement>(null);
  const onBoard = new Map<string, number>();
  for (const widget of board.widgets) onBoard.set(widget.kind, (onBoard.get(widget.kind) ?? 0) + 1);
  const wanted = query.trim().toLowerCase();

  const groups: KindGroup[] = [];
  const byId = new Map<string, KindGroup>();
  const order = ['mu3lab', ...CATEGORIES.map((category) => category.id)];
  for (const kind of board.kinds) {
    const info = widgetInfo(kind.kind, board.kinds);
    const text = `${info.title} ${info.description} ${info.appName ?? 'Mu3Lab'}`.toLowerCase();
    if (wanted && !text.includes(wanted)) continue;
    const category = info.appId ? services.find((service) => service.id === info.appId)?.category || 'other' : 'mu3lab';
    let group = byId.get(category);
    if (!group) {
      group = { id: category, label: category === 'mu3lab' ? 'Mu3Lab' : categoryLabel(category), kinds: [] };
      byId.set(category, group);
      groups.push(group);
    }
    group.kinds.push(kind);
  }
  const rank = (id: string) => (order.includes(id) ? order.indexOf(id) : order.length);
  groups.sort((a, b) => rank(a.id) - rank(b.id) || a.label.localeCompare(b.label));
  const isOpen = (group: KindGroup) => Boolean(wanted) || opened.has(group.id);

  // Every name can be dragged onto the board, at the size it would be added at.
  const openKey = groups
    .filter(isOpen)
    .map((group) => group.id)
    .join(',');
  useEffect(() => {
    const entries = [...(list.current?.querySelectorAll<HTMLElement>('li[data-kind]') ?? [])].filter(
      (entry) => !entry.hasAttribute('data-added'),
    );
    if (!entries.length) return;
    Grid.setupDragIn(
      entries,
      { appendTo: 'body', helper: 'clone', handle: '.board-kind-row' },
      entries.map((entry) => {
        const kind = board.kinds.find((item) => item.kind === entry.dataset.kind);
        return {
          id: `drop:${entry.dataset.kind}`,
          w: kind ? scaled(kind.w, board.columns, board.base) : 3,
          h: kind?.h ?? 2,
        };
      }),
    );
  }, [board.kinds, board.columns, board.base, wanted, openKey, chosen, onBoard.size]);

  const toggle = (id: string) =>
    setOpened((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <aside ref={list} className="details-card board-panel" aria-label="Add a widget">
      <header className="board-panel-head">
        <h3>Add a widget</h3>
        <Button variant="ghost" size="sm" icon={X} className="btn-icon" aria-label="Close" onClick={onClose} />
      </header>
      <label className="board-kinds-search">
        <Search aria-hidden />
        <input
          type="search"
          value={query}
          placeholder="Search widgets"
          aria-label="Search widgets"
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <p className="muted board-kinds-hint">Choose one to see it, or drag it onto your board.</p>
      {groups.length === 0 && <p className="muted">No widget matches “{query}”.</p>}
      <div className="board-kinds-groups">
        {groups.map((group) => {
          const open = isOpen(group);
          return (
            <section key={group.id} className="board-kinds-group" aria-label={group.label}>
              <button
                type="button"
                className="board-kinds-head"
                aria-expanded={open}
                disabled={Boolean(wanted)}
                onClick={() => toggle(group.id)}
              >
                <ChevronRight className="board-kinds-chevron" aria-hidden />
                <span>{group.label}</span>
                <small>{group.kinds.length}</small>
              </button>
              {open && (
                <ul className="board-kinds">
                  {group.kinds.map((kind) => {
                    const info = widgetInfo(kind.kind, board.kinds);
                    const Icon = info.icon;
                    const count = onBoard.get(kind.kind) ?? 0;
                    const added = ONCE.has(kind.kind) && count > 0;
                    const shown = chosen === kind.kind;
                    return (
                      <li
                        key={kind.kind}
                        data-kind={kind.kind}
                        data-added={added ? '' : undefined}
                        className={shown ? 'is-chosen' : undefined}
                      >
                        <button
                          type="button"
                          className="board-kind-row"
                          aria-expanded={shown}
                          title={added ? undefined : 'Choose to see it, or drag it onto your board'}
                          onClick={() => setChosen(shown ? null : kind.kind)}
                        >
                          <Icon className="board-kind-icon" aria-hidden />
                          <span className="board-kind-name">
                            {info.title}
                            {info.appName && <small>{info.appName}</small>}
                          </span>
                          {count > 0 && (
                            <span className="board-kind-count" title="On your board">
                              {count > 1 ? `${count} on board` : 'On board'}
                            </span>
                          )}
                        </button>
                        {shown && (
                          <div className="board-kind-detail">
                            {NO_PREVIEW.has(kind.kind) ? null : <Preview kind={kind} board={board} />}
                            <p>{info.description}</p>
                            <Button
                              variant="primary"
                              size="sm"
                              icon={added ? undefined : Plus}
                              disabled={added}
                              onClick={() => {
                                const id = board.add(kind);
                                if (info.Settings) onPick(id);
                              }}
                            >
                              {added ? 'Already on your board' : 'Add to board'}
                            </Button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          );
        })}
      </div>
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
  const [measured, setMeasured] = useState({ shift: 0, covered: false, reserve: 0 });
  useEffect(() => {
    const element = home.current;
    const board = element?.querySelector<HTMLElement>('.board');
    const inner = element?.parentElement;
    if (!open || !element || !board || !inner) return;
    // A page as wide as the window (Home's): the board narrows beside the panel instead of sliding.
    if (getComputedStyle(inner).maxWidth === 'none') {
      const fit = () =>
        setMeasured({ shift: 0, covered: false, reserve: document.documentElement.clientWidth > 1000 ? PANEL : 0 });
      fit();
      window.addEventListener('resize', fit);
      return () => window.removeEventListener('resize', fit);
    }
    resetSidebarRoom();
    // The board keeps the width it has now, even if the sidebar folds and the page gets wider.
    board.style.width = `${board.offsetWidth}px`;
    // Where the board will sit, worked out from the layout's own sizes, so nothing waits for the
    // sidebar to finish folding: the board and the panel move together, once.
    const measure = () => {
      const width = document.documentElement.clientWidth;
      if (width <= 1000) {
        setMeasured({ shift: 0, covered: false, reserve: 0 });
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
      setMeasured({ shift: Math.max(0, Math.min(needed, room)), covered: needed > room, reserve: 0 });
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
  return {
    home,
    shift: open ? measured.shift : 0,
    covered: open && measured.covered,
    reserve: open ? measured.reserve : 0,
  };
}

/** What edit mode's side panel shows: nothing, the widgets to add, or one widget's settings (its id). */
export type Panel = 'closed' | 'add' | string;

interface CardContext {
  board: BoardState;
  picked?: string;
  onPick: (id: string) => void;
  onConfigure: (id: string) => void;
  onKeys: (widget: HomeWidget) => (event: KeyboardEvent) => void;
}

/** What every grid cell needs, shared so a change (edit mode, a setting) reaches cells without rebuilding the grid. */
const Cards = createContext<CardContext | null>(null);

/** One grid cell: the grid knows only the widget's id, and the card finds the rest on the board. */
function Cell({ id }: { id?: unknown }) {
  const context = useContext(Cards);
  const widget = context?.board.widgets.find((item) => item.id === id);
  if (!context || !widget) return null;
  return (
    <WidgetCard
      widget={widget}
      board={context.board}
      current={widget.id === context.picked}
      stacked={false}
      onPick={context.onPick}
      onConfigure={context.onConfigure}
      onKeys={context.onKeys}
    />
  );
}

const CELLS = { Cell } as ComponentMap;

/** The width of an element, followed as the page or the edit panel changes it. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  // Measured before the first paint, so the board never flashes the wrong layout.
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.clientWidth);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/**
 * The board on gridstack. Other widgets move only once the dragged one covers more than half of them,
 * a widget dropped onto one of the same size swaps with it, and in free placement the ones pushed aside
 * slide back when it moves away. The grid owns positions while someone drags; every change it reports
 * is one undoable step, and anything else (undo, cancel, adding) is drawn back into the grid.
 */
/** The board's widgets as gridstack nodes: where each sits, its size limits, and the cell that draws it. */
function nodesOf(board: BoardState) {
  return board.widgets.map((widget) => {
    const kind = board.kindOf(widget.kind);
    const limit = kind ? limitsFor(kind, board.columns, board.base) : undefined;
    return {
      id: widget.id,
      x: widget.x,
      y: widget.y,
      w: widget.w,
      h: widget.h,
      ...limit,
      component: 'Cell',
      props: { id: widget.id },
    };
  });
}

function GridBoard({ board }: { board: BoardState }) {
  const handle = useRef<GridStackHandle>(null);
  // Changes the grid reports while it is being redrawn from the board are not the person's.
  const redrawing = useRef(false);
  const latest = useRef(board);
  useLayoutEffect(() => {
    latest.current = board;
  });
  const { placement, editing, revision, tidyRequest } = board;

  // Read once: from then on the grid is told about changes, never rebuilt.
  const [options] = useState<GridStackOptions>(() => ({
    column: board.columns,
    cellHeight: ROW + GAP,
    margin: GAP / 2,
    staticGrid: !board.editing,
    mode: board.placement === 'gravity' ? 'top' : 'float',
    draggable: { handle: '.widget-head', cancel: 'button, a, input, select, textarea' },
    resizable: { handles: 'e,se,s' },
    engineClass: SwappingEngine,
    // Widgets dragged in from the Add list (AddList); see `dropped` below.
    acceptWidgets: (element: Element) => element.hasAttribute('data-kind'),
    children: nodesOf(board),
  }));

  const grid = () => handle.current?.getGrid() ?? null;

  useEffect(() => {
    grid()?.setStatic(!editing);
  }, [editing]);

  useEffect(() => {
    grid()?.mode(placement === 'gravity' ? 'top' : 'float');
  }, [placement]);

  // Undo, redo, cancel, adding and removing: the grid takes the board as it now is.
  const drawn = useRef(revision);
  useEffect(() => {
    const current = grid();
    if (!current || drawn.current === revision) return;
    drawn.current = revision;
    const now = latest.current;
    redrawing.current = true;
    try {
      current.batchUpdate();
      if (current.getColumn() !== now.columns) current.column(now.columns, 'none');
      current.load(nodesOf(now), true);
      current.batchUpdate(false);
    } finally {
      redrawing.current = false;
    }
  }, [revision]);

  // Tidy up: every widget moves straight up as far as it goes, closing the gaps but never changing
  // columns (one undoable step). Packing to the top for a moment does exactly that.
  const tidied = useRef(tidyRequest);
  useEffect(() => {
    if (tidied.current === tidyRequest) return;
    tidied.current = tidyRequest;
    grid()?.mode('top').mode('float');
  }, [tidyRequest]);

  // A drop from the Add list arrives as two reports (the widgets it pushed, and the drop itself), in
  // either order: whichever comes second joins the first, so one Undo takes back the whole drop.
  const lastReport = useRef(0);
  const lastDrop = useRef(0);
  const JOINED = 300;

  const report = useCallback((_event: Event, changed: GridStackNode[]) => {
    if (redrawing.current || !latest.current.editing) return;
    lastReport.current = performance.now();
    latest.current.place(
      changed
        .filter((node) => node.id !== undefined)
        .map((node): Spot => ({ id: String(node.id), x: node.x ?? 0, y: node.y ?? 0, w: node.w ?? 1, h: node.h ?? 1 })),
      performance.now() - lastDrop.current < JOINED,
    );
  }, []);

  // A widget dragged in from the Add list: gridstack drops a stand-in where it landed; the board adds
  // the real widget there (one undoable step) and the stand-in goes.
  const dropped = useCallback((_event: Event, _before: GridStackNode | undefined, node: GridStackNode) => {
    const now = latest.current;
    const name = String(node.id ?? '').replace(/^drop:/, '');
    if (node.el) grid()?.removeWidget(node.el, true, false);
    const kind = now.kinds.find((item) => item.kind === name);
    const joined = performance.now() - lastReport.current < JOINED;
    lastDrop.current = performance.now();
    if (kind && now.editing) now.add(kind, { x: node.x ?? 0, y: node.y ?? 0 }, joined);
  }, []);

  return (
    <GridStack
      ref={handle}
      options={options}
      components={CELLS}
      onChange={report}
      onDropped={dropped}
      className="board-grid"
    />
  );
}

/** The person's Home board: widgets where they put them, and Edit mode to move, resize and add them. */
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
  const [frame, width] = useWidth();
  const onKeys = useKeyboardArrange(board);
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
  // A phone-width board: one column, top-left first, as tall as each widget's rows. (Before it has a
  // width at all, which only happens without a real page layout, it is drawn the same way.)
  const stacked = width < STACKED;
  const ordered = useMemo(
    () =>
      [...board.widgets]
        .filter((widget) => board.editing || !widget.hide_on_phone)
        .sort((a, b) => a.y - b.y || a.x - b.x),
    [board.widgets, board.editing],
  );
  const context = useMemo<CardContext>(
    () => ({ board, picked: picked?.id, onPick: pick, onConfigure: configure, onKeys }),
    [board, picked?.id, pick, configure, onKeys],
  );
  const showPanel = board.editing && panel !== 'closed';
  let content: ReactNode;
  if (!board.loaded)
    content = board.error ? (
      <EmptyState title="Your board could not be loaded">{board.error}</EmptyState>
    ) : (
      <p className="muted">Loading your board…</p>
    );
  else if (stacked)
    content = (
      <div className="board-stack">
        {ordered.map((widget) => (
          <WidgetCard
            key={widget.id}
            widget={widget}
            board={board}
            current={widget.id === picked?.id}
            stacked
            onPick={pick}
            onConfigure={configure}
            onKeys={onKeys}
          />
        ))}
        {board.editing && <p className="muted board-stack-note">Move and resize widgets on a wider screen.</p>}
      </div>
    );
  else
    content = (
      <>
        {/* The grid stays mounted while empty, so the first widget added has somewhere to go. */}
        <GridBoard board={board} />
        {!board.widgets.length && (
          <EmptyState title="Your board is empty">
            {board.editing ? 'Add a widget from the list.' : 'Choose Edit to add weather, your calendar and more.'}
          </EmptyState>
        )}
      </>
    );
  return (
    <div ref={frame} className="board-frame">
      <Cards.Provider value={context}>
        <div className={`board-layout${showPanel ? ' has-panel' : ''}`}>
          <div className={`board${board.editing ? ' is-editing' : ''}`}>{content}</div>
          {/* In the body, not the page: the page slides to make room, and a slid page would carry the panel along. */}
          {showPanel &&
            createPortal(
              <EditPanel
                board={board}
                picked={picked}
                onPick={pick}
                onClose={() => setPanel('closed')}
                finish={finish}
              />,
              document.body,
            )}
        </div>
      </Cards.Provider>
    </div>
  );
}
