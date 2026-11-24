import { useCallback, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { deleteApi, type HomeBoard, type HomeWidget, type HomeWidgetKind, putJsonApi } from '../../../api';
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
}

/**
 * The signed-in person's board. Edit mode works on a copy: Done saves it, Cancel drops it, so a
 * half-made arrangement never reaches the server or another screen. Every change while editing can be
 * undone and redone.
 */
export function useBoard() {
  const { data, error, loading, reload } = useApi<HomeBoard>('/api/v1/home/board');
  const [editor, setEditor] = useState<Editor>({ draft: null, past: [], future: [], revision: 0 });
  const [saving, setSaving] = useState(false);
  // Asks the grid to pack every widget up; the grid reports the result as one undoable step.
  const [tidyRequest, setTidyRequest] = useState(0);
  const { draft } = editor;
  const editing = draft !== null;
  const saved: Draft = useMemo(
    () => ({ columns: data?.columns ?? BASE, placement: data?.placement ?? 'free', widgets: data?.widgets ?? [] }),
    [data],
  );
  const current = draft ?? saved;
  const base = data?.base_columns ?? BASE;
  const kinds = useMemo(() => data?.kinds ?? [], [data]);

  const kindOf = useCallback((kind: string) => kinds.find((item) => item.kind === kind), [kinds]);

  /**
   * One undoable change. `redraw`: the grid must take the new state (it did not make the change itself).
   * `amend`: part of the step just taken (a widget dropped in, and the ones it pushed aside), so one
   * Undo takes back both.
   */
  const commit = useCallback((change: (draft: Draft) => Draft | null, redraw = true, amend = false) => {
    setEditor((state) => {
      if (state.draft === null) return state;
      const next = change(state.draft);
      if (next === null || next === state.draft) return state;
      return {
        draft: next,
        past: amend && state.past.length ? state.past : [...state.past, state.draft].slice(-HISTORY),
        future: [],
        revision: redraw ? state.revision + 1 : state.revision,
      };
    });
  }, []);

  const startEditing = useCallback(
    () => setEditor((state) => ({ draft: saved, past: [], future: [], revision: state.revision })),
    [saved],
  );
  const cancel = useCallback(
    () => setEditor((state) => ({ draft: null, past: [], future: [], revision: state.revision + 1 })),
    [],
  );
  const undo = useCallback(
    () =>
      setEditor((state) =>
        state.draft === null || !state.past.length
          ? state
          : {
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
              draft: state.future[0],
              past: [...state.past, state.draft],
              future: state.future.slice(1),
              revision: state.revision + 1,
            },
      ),
    [],
  );

  const add = useCallback(
    (kind: WidgetKind, at?: { x: number; y: number }, amend = false) => {
      const id = newId(kind.kind);
      commit(
        (draft) => {
          const w = scaled(kind.w, draft.columns, base);
          const wanted = at && {
            x: Math.max(0, Math.min(draft.columns - w, at.x)),
            y: Math.max(0, at.y),
            w,
            h: kind.h,
          };
          // Where it was dropped when that space is free; otherwise the first free space from the top.
          const spot =
            wanted && !draft.widgets.some((other) => overlaps(other, wanted))
              ? wanted
              : firstFree(draft.widgets, w, kind.h, draft.columns);
          const widget: Widget = { id, kind: kind.kind, ...spot, hide_on_phone: false, settings: {} };
          return { ...draft, widgets: [...draft.widgets, widget] };
        },
        true,
        amend,
      );
      return id;
    },
    [commit, base],
  );
  /** A copy with the same settings, in the first free space. */
  const duplicate = useCallback(
    (id: string) =>
      commit((draft) => {
        const source = draft.widgets.find((item) => item.id === id);
        if (!source) return null;
        const spot = firstFree(draft.widgets, source.w, source.h, draft.columns);
        return { ...draft, widgets: [...draft.widgets, { ...source, id: newId(source.kind), ...spot }] };
      }),
    [commit],
  );
  const remove = useCallback(
    (id: string) => commit((draft) => ({ ...draft, widgets: draft.widgets.filter((item) => item.id !== id) })),
    [commit],
  );
  const update = useCallback(
    (id: string, patch: Partial<Widget>) =>
      commit(
        (draft) => ({
          ...draft,
          widgets: draft.widgets.map((item) => (item.id === id ? { ...item, ...patch } : item)),
        }),
        ['x', 'y', 'w', 'h'].some((key) => key in patch),
      ),
    [commit],
  );
  /** The grid's arrangement after a drag, a resize or tidying up. */
  const place = useCallback(
    (spots: readonly Spot[], amend = false) =>
      commit(
        (draft) => {
          const at = new Map(spots.map((spot) => [spot.id, spot]));
          let moved = false;
          const widgets = draft.widgets.map((item) => {
            const spot = at.get(item.id);
            if (!spot || (spot.x === item.x && spot.y === item.y && spot.w === item.w && spot.h === item.h))
              return item;
            moved = true;
            return { ...item, x: spot.x, y: spot.y, w: spot.w, h: spot.h };
          });
          return moved ? { ...draft, widgets } : null;
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
            const kind = kinds.find((value) => value.kind === item.kind);
            const limit = kind ? limitsFor(kind, next, base) : { minW: 1, maxW: next };
            const w = Math.max(limit.minW, Math.min(limit.maxW, Math.round(item.w * ratio)));
            return { ...item, w, x: Math.max(0, Math.min(next - w, Math.round(item.x * ratio))) };
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
      await putJsonApi<HomeBoard>('/api/v1/home/board', draft);
      await reload();
      setEditor((state) => ({ draft: null, past: [], future: [], revision: state.revision + 1 }));
      return true;
    } catch (cause) {
      toast.error(`Your board was not saved: ${errorText(cause)}`);
      return false;
    } finally {
      setSaving(false);
    }
  }, [draft, reload]);

  const reset = useCallback(async () => {
    try {
      const fresh = await deleteApi<HomeBoard>('/api/v1/home/board');
      await reload();
      // Starting over is one more step: Undo brings the person's own board back.
      commit(() => ({ columns: fresh.columns, placement: fresh.placement, widgets: fresh.widgets }));
    } catch (cause) {
      toast.error(`The board was not reset: ${errorText(cause)}`);
    }
  }, [reload, commit]);

  return {
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
    setColumns,
    setPlacement,
    reset,
  };
}

export type Board = ReturnType<typeof useBoard>;
