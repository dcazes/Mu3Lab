import {
  Check,
  ChevronDown,
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
  Ungroup,
  X,
} from 'lucide-react';
import type { GridItemHTMLElement, GridStackNode, GridStackWidget } from 'gridstack';
import 'gridstack/dist/gridstack.css';
import { DDManager, GridStack as Grid } from 'gridstack';
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
import {
  type Board as BoardState,
  collapsedLayout,
  everyWidget,
  isCollapsed,
  GROUP,
  isGroup,
  limitsFor,
  locate,
  overlaps,
  scaled,
  type Spot,
} from './useBoard';
import { SwappingEngine } from './gridEngine';
import './dragScroll';
import { refreshWidget } from './AppWidget';
import { BoxContext, useMeasured } from './size';
import { groupTitle } from './GroupSettings';
import { widgetInfo } from './widgets';
import { SettingsPopover } from './SettingsPopover';

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
  const { kindOf, update } = board;
  return (widget: HomeWidget) => (event: KeyboardEvent) => {
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    const kind = kindOf(widget.kind);
    if (!step || !kind) return;
    event.preventDefault();
    // In a group, it moves among the group's widgets, within the group's width.
    const group = locate(board.widgets, widget.id)?.group;
    const widgets = group?.children ?? board.widgets;
    const columns = group?.w ?? board.columns;
    const fixed = limitsFor(kind, board.columns, board.base);
    // A group is never narrower than its widgets reach.
    const limit = isGroup(widget) ? { ...fixed, minW: groupMinW(widget, fixed.minW) } : fixed;
    const next = event.shiftKey
      ? {
          ...widget,
          w: Math.max(limit.minW, Math.min(limit.maxW, columns - widget.x, widget.w + step[0])),
          // A group's height follows its widgets.
          h: isGroup(widget) ? widget.h : Math.max(limit.minH, Math.min(limit.maxH, widget.h + step[1])),
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
  // The person's own name for it, and its title bar left out outside edit mode.
  const title = widget.title || info.title;
  const untitled = Boolean(widget.hide_title) && !editing;
  // One row tall: the title sits beside the contents, which would have no room under it.
  const oneRow = widget.h === 1;
  const classes = [
    'widget',
    editing && 'is-editing',
    current && 'is-current',
    untitled && 'is-untitled',
    oneRow && 'is-one-row',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <article
      className={classes}
      data-widget-id={widget.id}
      // Stacked on a phone, the widget is as tall as its rows would be.
      style={stacked ? ({ height: widget.h * ROW + (widget.h - 1) * GAP } as CSSProperties) : undefined}
      aria-label={title}
    >
      {/* In edit mode the whole title bar is the handle that moves the widget. */}
      {!untitled && (
        <header className="widget-head">
          {editing && !stacked && (
            <span
              role="button"
              tabIndex={0}
              className="widget-grip"
              aria-label={`Move ${title}`}
              title="Drag the title bar to move. Arrow keys move it; Shift and arrow keys resize it."
              onKeyDown={onKeys(widget)}
            >
              <GripVertical />
            </span>
          )}
          <Icon className="widget-icon" aria-hidden />
          <h3>
            {editing ? (
              title
            ) : info.to ? (
              <Link to={info.to}>{title}</Link>
            ) : open ? (
              <a href={open.url} target="_blank" rel="noreferrer" title={`Open ${info.appName ?? 'the app'}`}>
                {title}
              </a>
            ) : (
              title
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
              aria-label={`${title} settings`}
              aria-haspopup="dialog"
              aria-expanded={current}
              data-settings-for={widget.id}
              onClick={() => onPick(widget.id)}
            >
              <Settings2 />
            </button>
          )}
          <Menu items={menu} label={`${title} actions`} compact />
          {editing && (
            <button
              type="button"
              className="widget-tool"
              aria-label={`Remove ${title}`}
              onClick={() => board.remove(widget.id)}
            >
              <X />
            </button>
          )}
        </header>
      )}
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

/**
 * A group's card: its title bar (which moves it in edit mode) over a grid of its own, which gridstack
 * draws in the same cell (``subGridOpts`` in ``nodesOf``). The card is kept first in the cell, so the
 * title bar, not a widget inside, is the group's handle.
 */
function GroupCard({
  widget,
  board,
  current,
  onPick,
  onKeys,
}: {
  widget: HomeWidget;
  board: BoardState;
  current: boolean;
  onPick: (id: string) => void;
  onKeys: (widget: HomeWidget) => (event: KeyboardEvent) => void;
}) {
  const card = useRef<HTMLElement>(null);
  const { editing } = board;
  useLayoutEffect(() => {
    const element = card.current;
    const cell = element?.parentElement;
    if (element && cell && cell.firstElementChild !== element) cell.prepend(element);
  });
  const title = groupTitle(widget);
  const empty = !widget.children?.length;
  // Folded outside edit mode only: while arranging, every group is open, as it really is.
  const collapsed = isCollapsed(widget) && !editing;
  const classes = [
    'widget',
    'widget-group',
    editing && 'is-editing',
    current && 'is-current',
    collapsed && 'is-collapsed',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <article ref={card} className={classes} data-widget-id={widget.id} aria-label={title}>
      <header className="widget-head">
        {editing && (
          <span
            role="button"
            tabIndex={0}
            className="widget-grip"
            aria-label={`Move ${title}`}
            title="Drag the title bar to move the group and everything in it. Arrow keys move it."
            onKeyDown={onKeys(widget)}
          >
            <GripVertical />
          </span>
        )}
        {!editing && (
          <button
            type="button"
            className="widget-tool widget-group-fold"
            aria-expanded={!collapsed}
            aria-label={collapsed ? `Show ${title}` : `Fold ${title}`}
            title={collapsed ? 'Show the widgets in this group' : 'Fold this group to its title'}
            onClick={() => void board.setCollapsed(widget.id, !collapsed)}
          >
            <ChevronDown />
          </button>
        )}
        {editing ? (
          // Renamed right here: the field is only as wide as the name, so the rest of the bar still moves the group.
          <input
            className="widget-group-title"
            aria-label="Group name"
            maxLength={40}
            value={String((widget.settings as { title?: string }).title ?? '')}
            placeholder="Group"
            size={Math.max(4, title.length + 1)}
            onChange={(event) =>
              board.update(widget.id, { settings: { ...widget.settings, title: event.target.value } })
            }
            onKeyDown={(event) => {
              if (event.key === 'Enter') event.currentTarget.blur();
            }}
          />
        ) : (
          <h3>{title}</h3>
        )}
        {collapsed && !empty && (
          <span className="widget-group-count muted">
            {widget.children?.length} widget{widget.children?.length === 1 ? '' : 's'}
          </span>
        )}
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
            aria-label={`${title} settings`}
            aria-haspopup="dialog"
            aria-expanded={current}
            data-settings-for={widget.id}
            onClick={() => onPick(widget.id)}
          >
            <Settings2 />
          </button>
        )}
        {editing && (
          <Menu
            items={[{ label: 'Duplicate', icon: Copy, onSelect: () => board.duplicate(widget.id) }]}
            label={`${title} actions`}
            compact
          />
        )}
        {editing && (
          <button
            type="button"
            className="widget-tool"
            aria-label={`Ungroup ${title}`}
            title="Remove the group; its widgets stay on the board"
            onClick={() => board.remove(widget.id)}
          >
            <Ungroup />
          </button>
        )}
      </header>
      {empty && !collapsed && (
        <p className="muted widget-group-empty">
          {editing ? 'Drag widgets here by their title bar.' : 'Nothing in this group yet.'}
        </p>
      )}
    </article>
  );
}

/** A group on a phone: its title, then its widgets one under another. */
function GroupStack({
  widget,
  board,
  picked,
  onPick,
  onConfigure,
  onKeys,
}: {
  widget: HomeWidget;
  board: BoardState;
  picked?: string;
  onPick: (id: string) => void;
  onConfigure: (id: string) => void;
  onKeys: (widget: HomeWidget) => (event: KeyboardEvent) => void;
}) {
  const collapsed = isCollapsed(widget) && !board.editing;
  const children = collapsed
    ? []
    : [...(widget.children ?? [])]
        .filter((child) => board.editing || !child.hide_on_phone)
        .sort((a, b) => a.y - b.y || a.x - b.x);
  const title = groupTitle(widget);
  return (
    <section className={`board-stack-group${collapsed ? ' is-collapsed' : ''}`} aria-label={title}>
      <header className="board-stack-group-head">
        {!board.editing && (
          <button
            type="button"
            className="widget-tool widget-group-fold"
            aria-expanded={!collapsed}
            aria-label={collapsed ? `Show ${title}` : `Fold ${title}`}
            onClick={() => void board.setCollapsed(widget.id, !collapsed)}
          >
            <ChevronDown />
          </button>
        )}
        {board.editing ? (
          <input
            className="widget-group-title"
            aria-label="Group name"
            maxLength={40}
            value={String((widget.settings as { title?: string }).title ?? '')}
            placeholder="Group"
            onChange={(event) =>
              board.update(widget.id, { settings: { ...widget.settings, title: event.target.value } })
            }
          />
        ) : (
          <h3>{title}</h3>
        )}
        {board.editing && (
          <button
            type="button"
            className="widget-tool"
            aria-label={`${title} settings`}
            aria-haspopup="dialog"
            aria-expanded={picked === widget.id}
            data-settings-for={widget.id}
            onClick={() => onPick(widget.id)}
          >
            <Settings2 />
          </button>
        )}
      </header>
      {children.map((child) => (
        <WidgetCard
          key={child.id}
          widget={child}
          board={board}
          current={child.id === picked}
          stacked
          onPick={onPick}
          onConfigure={onConfigure}
          onKeys={onKeys}
        />
      ))}
    </section>
  );
}

/** Edit mode's side panel: the widgets to add. (A widget's own settings drop down from its gear.) */
function EditPanel({
  board,
  onPick,
  onClose,
  finish,
}: {
  board: BoardState;
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
  return <AddList board={board} onPick={onPick} onClose={onClose} footer={footer} />;
}

// Widgets whose preview would do something (the camera starts its stream), or show nothing on their
// own (a group), show their description only.
const NO_PREVIEW = new Set(['camera', GROUP]);

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
  for (const widget of everyWidget(board.widgets)) onBoard.set(widget.kind, (onBoard.get(widget.kind) ?? 0) + 1);
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
                                if (info.Settings && info.setUpOnAdd !== false) onPick(id);
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

/**
 * The edit panel floats over the board's right edge (a bottom sheet on phones): the board keeps its
 * width and columns while the panel opens and closes, so what you arrange is what Home shows after
 * Done. Widgets under the panel can still be reached by scrolling the board or closing the panel.
 * ``covered``: on a computer the panel sits over the toolbar's end, so it carries Cancel and Done itself.
 */
export function usePanelRoom(open: boolean) {
  const home = useRef<HTMLDivElement>(null);
  const [wideWindow, setWideWindow] = useState(() => document.documentElement.clientWidth > 1000);
  useEffect(() => {
    if (!open) return;
    const fit = () => setWideWindow(document.documentElement.clientWidth > 1000);
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [open]);
  return { home, covered: open && wideWindow };
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
  const widget = context && locate(context.board.widgets, String(id))?.widget;
  if (!context || !widget) return null;
  if (isGroup(widget))
    return (
      <GroupCard
        widget={widget}
        board={context.board}
        current={widget.id === context.picked}
        onPick={context.onPick}
        onKeys={context.onKeys}
      />
    );
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
// What each grid takes when something is dragged onto it: the board takes widgets from the Add list
// and from groups; a group takes them too, but never another group.
const ontoBoard = (element: Element) =>
  element.hasAttribute('data-kind') || element.classList.contains('grid-stack-item');
const intoGroup = (element: Element) =>
  element.hasAttribute('data-kind')
    ? element.getAttribute('data-kind') !== GROUP
    : element.classList.contains('grid-stack-item') && !element.classList.contains('board-group-item');

/** A group is at least as wide as its widgets reach. */
function groupMinW(group: { children?: readonly { x: number; w: number }[] }, floor = 1) {
  return Math.max(floor, ...(group.children ?? []).map((child) => child.x + child.w));
}

/** After its widgets moved or resized, a group's narrowest width follows them. */
function refitGroupMin(grid: Grid | undefined) {
  const parent = grid?.parentGridNode;
  if (!grid || !parent?.el || !parent.grid) return;
  const reach = Math.max(1, ...grid.engine.nodes.map((node) => (node.x ?? 0) + (node.w ?? 1)));
  if (parent.minW !== reach) parent.grid.update(parent.el, { minW: reach });
}

/** The board's widgets as gridstack nodes: where each sits, its size limits, and the cell that draws it. */
/** What the grid draws: the board as it is while arranging, else with folded groups' room given back. */
const shownOf = (board: BoardState) => (board.editing ? board.widgets : collapsedLayout(board.widgets));

function nodesOf(board: BoardState, widgets: readonly HomeWidget[] = shownOf(board)): GridStackWidget[] {
  return widgets.map((widget) => {
    const kind = board.kindOf(widget.kind);
    const limit = kind ? limitsFor(kind, board.columns, board.base) : undefined;
    const node: GridStackWidget = {
      id: widget.id,
      x: widget.x,
      y: widget.y,
      w: widget.w,
      h: widget.h,
      ...limit,
      component: 'Cell',
      props: { id: widget.id },
    } as GridStackWidget;
    if (!isGroup(widget)) return node;
    // Folded (outside edit mode): one row, its title bar; its widgets are hidden with it.
    if (isCollapsed(widget) && !board.editing)
      return {
        ...node,
        h: 1,
        minH: 1,
        sizeToContent: false,
        class: 'board-group-item',
        subGridOpts: {
          children: nodesOf(board, widget.children ?? []),
          column: 'auto',
          layout: 'none',
          minRow: 1,
          class: 'board-subgrid',
          acceptWidgets: intoGroup,
        },
      } as GridStackWidget;
    // A group is as tall as its title and its widgets (sizeToContent); its grid has as many columns
    // as the group is wide, so a widget is the same size in it as on the board. Resizing the group
    // leaves its widgets where they are and as big as they are ('none'), and it never gets narrower
    // than the widgets in it.
    return {
      ...node,
      minW: groupMinW(widget, node.minW),
      sizeToContent: true,
      class: 'board-group-item',
      subGridOpts: {
        children: nodesOf(board, widget.children ?? []),
        column: 'auto',
        layout: 'none',
        minRow: 1,
        class: 'board-subgrid',
        acceptWidgets: intoGroup,
      },
    };
  });
}

/**
 * A group grows and shrinks to fit its widgets, but not while a widget is being dragged: it would grow
 * under a widget dragged down through it, and the widget could never leave it that way. It fits its
 * widgets again as soon as the drag ends.
 */
const heldGroups = new Set<GridItemHTMLElement>();
Grid.resizeToContentCB = (element: GridItemHTMLElement) => {
  const node = element.gridstackNode;
  // A group whose grid is not made yet (it is being added) has nothing to measure.
  if (node?.subGridOpts && !node.subGrid) return;
  if (DDManager.dragElement) {
    heldGroups.add(element);
    return;
  }
  node?.grid?.resizeToContent(element);
};
const fitHeldGroups = () =>
  window.setTimeout(() => {
    for (const element of heldGroups) if (element.isConnected) element.gridstackNode?.grid?.resizeToContent(element);
    heldGroups.clear();
  }, 0);
if (typeof document !== 'undefined') {
  document.addEventListener('mouseup', fitHeldGroups, true);
  document.addEventListener('touchend', fitHeldGroups, true);
}

/** Which grid each widget is in now: '' for the board, else its group's id. */
function membersOf(grid: Grid) {
  const found = new Map<string, string>();
  for (const node of grid.engine.nodes) {
    found.set(String(node.id), '');
    for (const child of node.subGrid?.engine.nodes ?? []) found.set(String(child.id), String(node.id));
  }
  return found;
}

const membersWanted = (widgets: readonly HomeWidget[]) => {
  const found = new Map<string, string>();
  for (const widget of widgets) {
    found.set(widget.id, '');
    for (const child of widget.children ?? []) found.set(child.id, widget.id);
  }
  return found;
};

const sameMembers = (a: Map<string, string>, b: Map<string, string>) =>
  a.size === b.size && [...a].every(([id, group]) => b.get(id) === group);

/** The group a grid belongs to (its id), or undefined for the board itself. */
const groupOfGrid = (grid: Grid | undefined) => {
  const parent = grid?.parentGridNode;
  return parent ? String(parent.id) : undefined;
};

/** Every grid on the board: the board's own, then each group's. */
const allGrids = (grid: Grid) => [grid, ...grid.engine.nodes.flatMap((node) => (node.subGrid ? [node.subGrid] : []))];

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
    // The page scrolls under a dragged widget by the pointer alone (dragScroll.ts), not gridstack's way.
    draggable: { handle: '.widget-head', cancel: 'button, a, input, select, textarea', scroll: false },
    resizable: { handles: 'e,se,s' },
    engineClass: SwappingEngine,
    // Widgets dragged in from the Add list (AddList) or out of a group; see `added` below.
    acceptWidgets: ontoBoard,
    children: nodesOf(board),
  }));

  const grid = () => handle.current?.getGrid() ?? null;

  useEffect(() => {
    grid()?.setStatic(!editing);
  }, [editing]);

  useEffect(() => {
    const current = grid();
    if (current) for (const each of allGrids(current)) each.mode(placement === 'gravity' ? 'top' : 'float');
  }, [placement]);

  // Undo, redo, cancel, adding and removing: the grid takes the board as it now is. When a widget is
  // in a different grid than before (Undo after dragging it into a group), the grid is drawn afresh.
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
      if (!sameMembers(membersOf(current), membersWanted(now.widgets))) current.removeAll(true);
      current.load(nodesOf(now), true);
      current.batchUpdate(false);
    } finally {
      redrawing.current = false;
    }
  }, [revision]);

  // Folding or unfolding a group outside edit mode, and starting or ending edit mode while one is
  // folded: the grid takes the layout as Home now shows it (every widget slides to its place).
  const folded = board.editing
    ? ''
    : board.widgets
        .filter(isCollapsed)
        .map((widget) => widget.id)
        .join(',');
  const foldedDrawn = useRef(folded);
  useEffect(() => {
    const current = grid();
    if (!current || foldedDrawn.current === folded) return;
    foldedDrawn.current = folded;
    redrawing.current = true;
    try {
      // Same widgets, new places and heights: gridstack moves each one (animated), nothing is redrawn.
      // It handles one change at a time and pushes aside whatever is in the way, so the order matters:
      // first what shrinks or moves up, top first (into room already freed), then what grows or moves
      // down, bottom first (into room already left). Neither order ever lands on another widget.
      const at = new Map(current.engine.nodes.map((node) => [String(node.id), node]));
      const up: [GridStackNode, GridStackWidget][] = [];
      const down: [GridStackNode, GridStackWidget][] = [];
      for (const wanted of nodesOf(latest.current)) {
        const node = at.get(String(wanted.id));
        if (!node?.el) continue;
        const rising = (wanted.y ?? 0) <= (node.y ?? 0) && (wanted.h ?? 1) <= (node.h ?? 1);
        (rising ? up : down).push([node, wanted]);
      }
      up.sort(([a], [b]) => (a.y ?? 0) - (b.y ?? 0));
      down.sort(([, a], [, b]) => (b.y ?? 0) - (a.y ?? 0));
      current.batchUpdate();
      for (const [node, wanted] of [...up, ...down])
        if (node.el)
          current.update(node.el, {
            x: wanted.x,
            y: wanted.y,
            w: wanted.w,
            h: wanted.h,
            minH: wanted.minH,
            sizeToContent: wanted.sizeToContent,
          });
      current.batchUpdate(false);
    } finally {
      redrawing.current = false;
    }
  }, [folded]);

  // Tidy up: every widget moves straight up as far as it goes, closing the gaps but never changing
  // columns (one undoable step). Packing to the top for a moment does exactly that, in groups too.
  const tidied = useRef(tidyRequest);
  useEffect(() => {
    if (tidied.current === tidyRequest) return;
    tidied.current = tidyRequest;
    const current = grid();
    if (current) for (const each of allGrids(current)) each.mode('top').mode('float');
  }, [tidyRequest]);

  // The board's grid and each group's report here (gridstack sends a group's events to the board).
  // Each report is one list: the board's, or one group's.
  const report = useCallback((_event: Event, changed: GridStackNode[]) => {
    if (redrawing.current || !latest.current.editing) return;
    const lists = new Map<string | undefined, Spot[]>();
    const grids = new Set<Grid>();
    for (const node of changed) {
      if (node.id === undefined || !node.grid) continue;
      const group = groupOfGrid(node.grid);
      const spot = { id: String(node.id), x: node.x ?? 0, y: node.y ?? 0, w: node.w ?? 1, h: node.h ?? 1 };
      lists.set(group, [...(lists.get(group) ?? []), spot]);
      if (group !== undefined) grids.add(node.grid);
    }
    for (const [group, spots] of lists) latest.current.place(spots, true, group);
    for (const each of grids) refitGroupMin(each);
  }, []);

  // Something arrived in a grid. From the Add list, gridstack drops a stand-in where it landed: the
  // board adds the real widget there and the stand-in goes. A widget dragged into a group, out of one
  // or between two moves there. Both join the step the drag already made (one Undo takes it back).
  const added = useCallback((_event: Event, nodes: GridStackNode[]) => {
    const now = latest.current;
    if (redrawing.current || !now.editing) return;
    for (const node of nodes) {
      const id = String(node.id ?? '');
      const target = node.grid;
      const group = groupOfGrid(target);
      if (id.startsWith('drop:')) {
        const kind = now.kinds.find((item) => item.kind === id.slice(5));
        const at = { x: node.x ?? 0, y: node.y ?? 0 };
        // After gridstack has finished the drop it is reporting.
        queueMicrotask(() => {
          if (node.el) target?.removeWidget(node.el, true, false);
          if (kind && latest.current.editing) latest.current.add(kind, at, true, group);
        });
        continue;
      }
      const found = locate(now.widgets, id);
      if (found && found.group?.id !== group)
        now.move(id, group, { x: node.x ?? 0, y: node.y ?? 0, w: node.w ?? 1, h: node.h ?? 1 }, true);
    }
    // A widget joined one group and may have left another: both groups' narrowest widths follow.
    const root = handle.current?.getGrid();
    if (root) for (const each of allGrids(root).slice(1)) refitGroupMin(each);
  }, []);

  return (
    <GridStack
      ref={handle}
      options={options}
      components={CELLS}
      onChange={report}
      onAdded={added}
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
  const picked = board.editing ? locate(board.widgets, panel)?.widget : undefined;
  // A gear opens its widget's settings, or closes them when they are already open.
  const pick = useCallback(
    (id: string | null) => setPanel(id === null ? 'add' : id === panel ? 'closed' : id),
    [setPanel, panel],
  );
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
  // The side panel is the Add list only; a picked widget's settings drop down from its gear.
  const showPanel = board.editing && panel === 'add';
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
        {ordered.map((widget) =>
          isGroup(widget) ? (
            <GroupStack
              key={widget.id}
              widget={widget}
              board={board}
              picked={picked?.id}
              onPick={pick}
              onConfigure={configure}
              onKeys={onKeys}
            />
          ) : (
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
          ),
        )}
        {board.editing && <p className="muted board-stack-note">Move and resize widgets on a wider screen.</p>}
      </div>
    );
  else
    content = (
      <>
        {/* The grid stays mounted while empty, so the first widget added has somewhere to go. */}
        {/* Each board (tab) gets a grid of its own. */}
        <GridBoard key={board.tab} board={board} />
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
          {/* In the body, over the page: opening it never changes the board's size. */}
          {showPanel &&
            createPortal(
              <EditPanel board={board} onPick={pick} onClose={() => setPanel('closed')} finish={finish} />,
              document.body,
            )}
          {picked && (
            <SettingsPopover key={picked.id} board={board} widget={picked} onClose={() => setPanel('closed')} />
          )}
        </div>
      </Cards.Provider>
    </div>
  );
}
