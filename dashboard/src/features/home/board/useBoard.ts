import { useCallback, useMemo, useState } from 'react';
import { toast } from 'sonner';
import {
  api,
  deleteApi,
  type HomeBoard,
  type HomeBoardTab,
  type HomeBoardTabs,
  type HomeWidget,
  type HomeWidgetKind,
  putJsonApi,
} from '../../../api';
import { errorText } from '../../../lib/format';
import { useApi } from '../../../lib/useApi';

export type Widget = HomeWidget;
export type WidgetKind = HomeWidgetKind;
/** free: widgets stay where they are put (gaps allowed); gravity: they pack to the top. */
export type Placement = HomeBoard['placement'];

// Ids are lowercase letters, digits and dashes (an app's kind, "app:widget", has a colon).
const newId = (kind: string) =>
  `${kind.replace(/[^a-z0-9-]/g, '-').slice(0, 30)}-${Math.random().toString(16).slice(2, 8)}`;
const BASE = 12;
// Undo remembers this many steps of one editing session.
const HISTORY = 100;
// The grid reports one drag as a few changes in a row (the widget, the ones it pushed, the group it
// left): changes this close together join one step.
const JOINED = 300;

/** A width written for the kinds' 12-column grid, on a board of `columns`. */
export function scaled(width: number, columns: number, base = BASE, up = false) {
  const exact = (width * columns) / base;
  return Math.max(1, Math.min(columns, up ? Math.ceil(exact) : Math.round(exact)));
}

/** A kind's size limits on a board of `columns` (the server's ``home_board.limits``). */
export function limitsFor(kind: WidgetKind, columns: number, base = BASE) {
  return {
    minW: scaled(kind.min_w, columns, base, true),
    maxW: scaled(kind.max_w, columns, base),
    minH: kind.min_h,
    maxH: kind.max_h,
  };
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/** The first space, row by row from the top, where a w×h widget fits without covering another. */
export function firstFree(widgets: readonly Box[], w: number, h: number, columns: number) {
  for (let y = 0; ; y += 1) {
    for (let x = 0; x + w <= columns; x += 1) {
      const spot = { x, y, w, h };
      if (!widgets.some((other) => overlaps(other, spot))) return spot;
    }
  }
}

/** A group: a titled card holding widgets on a small grid of its own (``children``). */
export const GROUP = 'group';
export const isGroup = (widget: Pick<Widget, 'kind'>) => widget.kind === GROUP;
// The group's title takes one row; an empty group keeps one row to drop widgets into.
const GROUP_MIN_H = 2;

/** A widget wherever it is: on the board, or in a group (then `group` is that group). */
export function locate(widgets: readonly Widget[], id: string): { widget: Widget; group?: Widget } | undefined {
  for (const widget of widgets) {
    if (widget.id === id) return { widget };
    const child = widget.children?.find((item) => item.id === id);
    if (child) return { widget: child, group: widget };
  }
  return undefined;
}

/** Every widget on the board, the ones in groups included. */
export const everyWidget = (widgets: readonly Widget[]) =>
  widgets.flatMap((widget) => [widget, ...(widget.children ?? [])]);

/** A group's height: its title row, then as far down as its widgets reach. */
export const groupHeight = (children: readonly Box[]) =>
  Math.max(GROUP_MIN_H, 1 + Math.max(0, ...children.map((child) => child.y + child.h)));

/** A group shows only its title bar, and the widgets below it move up while it stays that way. */
export const isCollapsed = (widget: Widget) =>
  isGroup(widget) && (widget.settings as { collapsed?: boolean }).collapsed === true;

/**
 * The board as Home draws it outside edit mode: each collapsed group one row tall, and every widget
 * moved up as far as the rows it freed allow, top first, without touching another. A widget can always
 * fall back to its own place (nothing moved there, as everything only moved up), so nothing overlaps,
 * and expanding the group puts every widget back where it was.
 */
export function collapsedLayout(widgets: readonly Widget[]): Widget[] {
  if (!widgets.some(isCollapsed)) return [...widgets];
  const sorted = [...widgets].sort((a, b) => a.y - b.y || a.x - b.x);
  const settled: Widget[] = [];
  for (const widget of sorted) {
    const own = isCollapsed(widget) ? { ...widget, h: 1 } : widget;
    let y = own.y;
    while (y > 0 && !settled.some((other) => overlaps(other, { ...own, y: y - 1 }))) y -= 1;
    settled.push({ ...own, y });
  }
  const at = new Map(settled.map((widget) => [widget.id, widget]));
  return widgets.map((widget) => at.get(widget.id) ?? widget);
}

/** Widgets that `fixed` now covers move down, as far as they must; so does anything they then cover. */
function pushedAside(widgets: Widget[], fixed: string): Widget[] {
  const settled = widgets.filter((widget) => widget.id === fixed);
  const rest = widgets.filter((widget) => widget.id !== fixed).sort((a, b) => a.y - b.y || a.x - b.x);
  for (const widget of rest) {
    let spot = widget;
    while (settled.some((other) => overlaps(other, spot))) spot = { ...spot, y: spot.y + 1 };
    settled.push(spot);
  }
  const at = new Map(settled.map((widget) => [widget.id, widget]));
  return widgets.map((widget) => at.get(widget.id) ?? widget);
}

/**
 * The board with one list of widgets changed: the board's own (no `group`), or a group's. A group
 * then takes the height its widgets need, and whatever it now covers moves down.
 */
function changeList(widgets: Widget[], group: string | undefined, change: (list: Widget[]) => Widget[]): Widget[] {
  if (!group) return change(widgets);
  let grown = false;
  const next = widgets.map((widget) => {
    if (widget.id !== group) return widget;
    const children = change(widget.children ?? []);
    const h = groupHeight(children);
    grown = h > widget.h;
    return { ...widget, children, h };
  });
  return grown ? pushedAside(next, group) : next;
}

/** How many columns a list of widgets has: the board's, or a group's width. */
const columnsOf = (draft: Draft, group?: string) =>
  group ? (draft.widgets.find((widget) => widget.id === group)?.w ?? draft.columns) : draft.columns;

const withNewIds = (widget: Widget): Widget => ({
  ...widget,
  id: newId(widget.kind),
  children: widget.children?.map((child) => ({ ...child, id: newId(child.kind) })),
});

interface Draft {
  columns: number;
  placement: Placement;
  widgets: Widget[];
}

/** Where each widget is: what the grid reports after a move or resize. */
export interface Spot extends Box {
  id: string;
}

interface Editor {
  draft: Draft | null;
  past: Draft[];
  future: Draft[];
  /** Bumped whenever the grid must be redrawn from this state (undo, cancel, add…), never after a drag. */
  revision: number;
  /** When the last step was taken (performance.now()), for joining a drag's reports into one step. */
  stamp: number;
}

/**
 * The signed-in person's board. Edit mode works on a copy: Done saves it, Cancel drops it, so a
 * half-made arrangement never reaches the server or another screen. Every change while editing can be
 * undone and redone.
 */
// The board shown last on this device, so Home opens where the person left it.
const TAB_KEY = 'mu3lab.home.tab';
const rememberedTab = () => {
  try {
    return localStorage.getItem(TAB_KEY) ?? '';
  } catch {
    return '';
  }
};

export function useBoard() {
  const [tab, setTabState] = useState(rememberedTab);
  const { data, error, loading, reload } = useApi<HomeBoard>(
    `/api/v1/home/board${tab ? `?tab=${encodeURIComponent(tab)}` : ''}`,
  );
  // A board shown outside edit mode can still fold or unfold a group: shown at once, then saved.
  const [folds, setFolds] = useState<ReadonlyMap<string, boolean>>(new Map());
  const shownTab = data?.tab ?? '';
  const [editor, setEditor] = useState<Editor>({ draft: null, past: [], future: [], revision: 0, stamp: 0 });
  const [saving, setSaving] = useState(false);
  // Asks the grid to pack every widget up; the grid reports the result as one undoable step.
  const [tidyRequest, setTidyRequest] = useState(0);
  const { draft } = editor;
  const editing = draft !== null;
  const saved: Draft = useMemo(() => {
    const widgets = (data?.widgets ?? []).map((widget) =>
      folds.has(widget.id) ? { ...widget, settings: { ...widget.settings, collapsed: folds.get(widget.id) } } : widget,
    );
    return { columns: data?.columns ?? BASE, placement: data?.placement ?? 'free', widgets };
  }, [data, folds]);
  const current = draft ?? saved;
  const base = data?.base_columns ?? BASE;
  const kinds = useMemo(() => data?.kinds ?? [], [data]);

  const kindOf = useCallback((kind: string) => kinds.find((item) => item.kind === kind), [kinds]);

  /**
   * One undoable change. `redraw`: the grid must take the new state (it did not make the change itself).
   * `amend`: a report from the grid, which joins the step taken just before it when that was part of the
   * same drag (a widget dropped in, and the ones it pushed aside), so one Undo takes back both.
   */
  const commit = useCallback((change: (draft: Draft) => Draft | null, redraw = true, amend = false) => {
    setEditor((state) => {
      if (state.draft === null) return state;
      const next = change(state.draft);
      if (next === null || next === state.draft) return state;
      const now = performance.now();
      const joins = amend && state.past.length > 0 && now - state.stamp < JOINED;
      return {
        draft: next,
        past: joins ? state.past : [...state.past, state.draft].slice(-HISTORY),
        future: [],
        revision: redraw ? state.revision + 1 : state.revision,
        stamp: now,
      };
    });
  }, []);

  const startEditing = useCallback(
    () => setEditor((state) => ({ ...state, draft: saved, past: [], future: [] })),
    [saved],
  );
  const cancel = useCallback(
    () => setEditor((state) => ({ ...state, draft: null, past: [], future: [], revision: state.revision + 1 })),
    [],
  );
  const undo = useCallback(
    () =>
      setEditor((state) =>
        state.draft === null || !state.past.length
          ? state
          : {
              ...state,
              draft: state.past[state.past.length - 1],
              past: state.past.slice(0, -1),
              future: [state.draft, ...state.future],
              revision: state.revision + 1,
            },
      ),
    [],
  );
  const redo = useCallback(
    () =>
      setEditor((state) =>
        state.draft === null || !state.future.length
          ? state
          : {
              ...state,
              draft: state.future[0],
              past: [...state.past, state.draft],
              future: state.future.slice(1),
              revision: state.revision + 1,
            },
      ),
    [],
  );

  /** A new widget: on the board, or in `group`; where it was dropped when that space is free. */
  const add = useCallback(
    (kind: WidgetKind, at?: { x: number; y: number }, amend = false, group?: string) => {
      const id = newId(kind.kind);
      const into = isGroup(kind) ? undefined : group;
      commit(
        (draft) => {
          const columns = columnsOf(draft, into);
          const w = Math.min(columns, scaled(kind.w, draft.columns, base));
          const h = isGroup(kind) ? groupHeight([]) : kind.h;
          const widget: Widget = {
            id,
            kind: kind.kind,
            x: 0,
            y: 0,
            w,
            h,
            hide_on_phone: false,
            settings: isGroup(kind) ? { title: 'Group' } : {},
            ...(isGroup(kind) ? { children: [] } : {}),
          };
          return {
            ...draft,
            widgets: changeList(draft.widgets, into, (list) => {
              const wanted = at && { x: Math.max(0, Math.min(columns - w, at.x)), y: Math.max(0, at.y), w, h };
              const spot =
                wanted && !list.some((other) => overlaps(other, wanted)) ? wanted : firstFree(list, w, h, columns);
              return [...list, { ...widget, ...spot }];
            }),
          };
        },
        true,
        amend,
      );
      return id;
    },
    [commit, base],
  );
  /** A copy with the same settings (a group's widgets too), in the first free space beside it. */
  const duplicate = useCallback(
    (id: string) =>
      commit((draft) => {
        const found = locate(draft.widgets, id);
        if (!found) return null;
        const { widget: source, group } = found;
        const columns = columnsOf(draft, group?.id);
        return {
          ...draft,
          widgets: changeList(draft.widgets, group?.id, (list) => [
            ...list,
            { ...withNewIds(source), ...firstFree(list, source.w, source.h, columns) },
          ]),
        };
      }),
    [commit],
  );
  /** Gone from the board. A group goes, but its widgets stay where they were, on the board itself. */
  const remove = useCallback(
    (id: string) =>
      commit((draft) => {
        const found = locate(draft.widgets, id);
        if (!found) return null;
        const { widget, group } = found;
        if (group)
          return {
            ...draft,
            widgets: changeList(draft.widgets, group.id, (list) => list.filter((item) => item.id !== id)),
          };
        const released = (widget.children ?? []).map((child) => ({
          ...child,
          x: widget.x + child.x,
          y: widget.y + 1 + child.y,
        }));
        return { ...draft, widgets: [...draft.widgets.filter((item) => item.id !== id), ...released] };
      }),
    [commit],
  );
  const update = useCallback(
    (id: string, patch: Partial<Widget>) =>
      commit(
        (draft) => {
          const found = locate(draft.widgets, id);
          if (!found) return null;
          return {
            ...draft,
            widgets: changeList(draft.widgets, found.group?.id, (list) =>
              list.map((item) => (item.id === id ? { ...item, ...patch } : item)),
            ),
          };
        },
        ['x', 'y', 'w', 'h'].some((key) => key in patch),
      ),
    [commit],
  );
  /** The grid's arrangement after a drag, a resize or tidying up: of the board, or of one group's widgets. */
  const place = useCallback(
    (spots: readonly Spot[], amend = false, group?: string) =>
      commit(
        (draft) => {
          const at = new Map(spots.map((spot) => [spot.id, spot]));
          let moved = false;
          const widgets = changeList(draft.widgets, group, (list) =>
            list.map((item) => {
              const spot = at.get(item.id);
              if (!spot || (spot.x === item.x && spot.y === item.y && spot.w === item.w && spot.h === item.h))
                return item;
              moved = true;
              // A group's height follows its widgets, whatever the grid measured.
              return { ...item, x: spot.x, y: spot.y, w: spot.w, h: isGroup(item) ? item.h : spot.h };
            }),
          );
          return moved ? { ...draft, widgets } : null;
        },
        false,
        amend,
      ),
    [commit],
  );
  /** A widget dragged into a group, out of one, or between two: it sits at `spot` in its new place. */
  const move = useCallback(
    (id: string, to: string | undefined, spot: Box, amend = false) =>
      commit(
        (draft) => {
          const found = locate(draft.widgets, id);
          if (!found || (to && isGroup(found.widget))) return null;
          const columns = columnsOf(draft, to);
          const w = Math.min(columns, spot.w);
          const moved = { ...found.widget, ...spot, w, x: Math.max(0, Math.min(columns - w, spot.x)) };
          const without = changeList(draft.widgets, found.group?.id, (list) => list.filter((item) => item.id !== id));
          return { ...draft, widgets: changeList(without, to, (list) => [...list, moved]) };
        },
        false,
        amend,
      ),
    [commit],
  );
  /** A finer or coarser grid: every widget keeps its place and its share of the width. */
  const setColumns = useCallback(
    (next: number) =>
      commit((draft) => {
        if (next === draft.columns) return null;
        const ratio = next / draft.columns;
        return {
          ...draft,
          columns: next,
          widgets: draft.widgets.map((item) => {
            const fit = (widget: Widget, columns: number) => {
              const kind = kinds.find((value) => value.kind === widget.kind);
              const limit = kind ? limitsFor(kind, next, base) : { minW: 1, maxW: next };
              const w = Math.min(columns, Math.max(limit.minW, Math.min(limit.maxW, Math.round(widget.w * ratio))));
              return { ...widget, w, x: Math.max(0, Math.min(columns - w, Math.round(widget.x * ratio))) };
            };
            const placed = fit(item, next);
            return placed.children
              ? { ...placed, children: placed.children.map((child) => fit(child, placed.w)) }
              : placed;
          }),
        };
      }),
    [commit, kinds, base],
  );
  const setPlacement = useCallback(
    (placement: Placement) => commit((draft) => (placement === draft.placement ? null : { ...draft, placement })),
    [commit],
  );

  const save = useCallback(async () => {
    if (draft === null) return true;
    setSaving(true);
    try {
      await putJsonApi<HomeBoard>('/api/v1/home/board', { ...draft, tab: shownTab });
      await reload();
      setEditor((state) => ({ ...state, draft: null, past: [], future: [], revision: state.revision + 1 }));
      return true;
    } catch (cause) {
      toast.error(`Your board was not saved: ${errorText(cause)}`);
      return false;
    } finally {
      setSaving(false);
    }
  }, [draft, reload, shownTab]);

  const reset = useCallback(async () => {
    try {
      const fresh = await deleteApi<HomeBoard>(
        `/api/v1/home/board${shownTab ? `?tab=${encodeURIComponent(shownTab)}` : ''}`,
      );
      await reload();
      // Starting over is one more step: Undo brings the person's own board back.
      commit(() => ({ columns: fresh.columns, placement: fresh.placement, widgets: fresh.widgets }));
    } catch (cause) {
      toast.error(`The board was not reset: ${errorText(cause)}`);
    }
  }, [reload, commit, shownTab]);

  /** Fold or unfold a group outside edit mode: drawn at once, saved with the board straight away. */
  const setCollapsed = useCallback(
    async (id: string, collapsed: boolean) => {
      if (!data || draft !== null) return;
      setFolds((current) => new Map(current).set(id, collapsed));
      const widgets = data.widgets.map((widget) =>
        widget.id === id ? { ...widget, settings: { ...widget.settings, collapsed } } : widget,
      );
      try {
        await putJsonApi<HomeBoard>('/api/v1/home/board', {
          columns: data.columns,
          placement: data.placement,
          widgets,
          tab: data.tab,
        });
        await reload();
      } catch (cause) {
        toast.error(`That was not saved: ${errorText(cause)}`);
      } finally {
        setFolds((current) => {
          const next = new Map(current);
          next.delete(id);
          return next;
        });
      }
    },
    [data, draft, reload],
  );

  /** Show another of the person's boards. While arranging, this board is saved first and arranging goes on there. */
  const switchTab = useCallback(
    async (id: string) => {
      if (id === (shownTab || tab)) return;
      const arranging = draft !== null;
      if (arranging && !(await save())) return;
      try {
        localStorage.setItem(TAB_KEY, id);
      } catch {
        // Remembering the board on this device is only a convenience.
      }
      setTabState(id);
      if (!arranging) return;
      try {
        const next = await api<HomeBoard>(`/api/v1/home/board?tab=${encodeURIComponent(id)}`);
        setEditor((state) => ({
          ...state,
          draft: { columns: next.columns, placement: next.placement, widgets: next.widgets },
          past: [],
          future: [],
          revision: state.revision + 1,
        }));
      } catch (cause) {
        toast.error(`That board could not be opened for arranging: ${errorText(cause)}`);
      }
    },
    [shownTab, tab, draft, save],
  );

  /** Rename, reorder, add or remove boards: the whole list, in order (a new one has no id). */
  const saveTabs = useCallback(
    async (wanted: HomeBoardTab[]) => {
      try {
        const result = await putJsonApi<HomeBoardTabs>('/api/v1/home/board/tabs', { tabs: wanted });
        const shown = shownTab || tab;
        if (!result.tabs.some((item) => item.id === shown)) {
          // The board on screen was removed: show the first one.
          if (draft !== null) cancel();
          setTabState(result.tabs[0].id ?? '');
        } else await reload();
        return result.tabs;
      } catch (cause) {
        toast.error(`Your boards were not changed: ${errorText(cause)}`);
        return null;
      }
    },
    [shownTab, tab, draft, cancel, reload],
  );

  return {
    tab: shownTab || tab,
    tabs: data?.tabs ?? [],
    switchTab,
    saveTabs,
    setCollapsed,
    widgets: current.widgets,
    columns: current.columns,
    placement: current.placement,
    kinds,
    kindOf,
    base,
    columnChoices: data?.column_choices ?? [4, 6, 8, 12, 16, 24],
    loaded: Boolean(data),
    error: data ? '' : error,
    loading,
    editing,
    saving,
    revision: editor.revision,
    canUndo: editor.past.length > 0,
    canRedo: editor.future.length > 0,
    tidyRequest,
    tidy: () => setTidyRequest((count) => count + 1),
    startEditing,
    cancel,
    save,
    undo,
    redo,
    add,
    remove,
    duplicate,
    update,
    place,
    move,
    setColumns,
    setPlacement,
    reset,
  };
}

export type Board = ReturnType<typeof useBoard>;
