import { useCallback, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { deleteApi, type HomeBoard, type HomeWidget, type HomeWidgetKind, putJsonApi } from '../../../api';
import { errorText } from '../../../lib/format';
import { useApi } from '../../../lib/useApi';

export type Widget = HomeWidget;
export type WidgetKind = HomeWidgetKind;

const newId = (kind: string) => `${kind}-${Math.random().toString(16).slice(2, 8)}`;

/**
 * The signed-in person's board. Edit mode works on a copy: Done saves it, Cancel drops it, so a
 * half-made arrangement never reaches the server or another screen.
 */
export function useBoard() {
  const { data, error, loading, reload } = useApi<HomeBoard>('/api/v1/home/board');
  const [draft, setDraft] = useState<Widget[] | null>(null);
  const [saving, setSaving] = useState(false);
  const editing = draft !== null;
  const widgets = draft ?? data?.widgets ?? [];
  const kinds = useMemo(() => data?.kinds ?? [], [data]);

  const kindOf = useCallback((kind: string) => kinds.find((item) => item.kind === kind), [kinds]);

  const startEditing = useCallback(() => setDraft(data?.widgets ?? []), [data]);
  const cancel = useCallback(() => setDraft(null), []);

  const change = useCallback((next: (current: Widget[]) => Widget[]) => {
    setDraft((current) => (current === null ? current : next(current)));
  }, []);

  const add = useCallback(
    (kind: WidgetKind) => {
      const widget: Widget = { id: newId(kind.kind), kind: kind.kind, w: kind.w, h: kind.h, settings: {} };
      // New widgets go first, where the person is looking.
      change((current) => [widget, ...current]);
      return widget.id;
    },
    [change],
  );
  const remove = useCallback((id: string) => change((current) => current.filter((item) => item.id !== id)), [change]);
  const update = useCallback(
    (id: string, patch: Partial<Widget>) =>
      change((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item))),
    [change],
  );
  const move = useCallback(
    (id: string, to: number) =>
      change((current) => {
        const from = current.findIndex((item) => item.id === id);
        if (from < 0) return current;
        const target = Math.max(0, Math.min(current.length - 1, to));
        if (target === from) return current;
        const next = [...current];
        const [item] = next.splice(from, 1);
        next.splice(target, 0, item);
        return next;
      }),
    [change],
  );

  const save = useCallback(async () => {
    if (draft === null) return true;
    setSaving(true);
    try {
      await putJsonApi<HomeBoard>('/api/v1/home/board', { widgets: draft });
      await reload();
      setDraft(null);
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
      setDraft(fresh.widgets);
    } catch (cause) {
      toast.error(`The board was not reset: ${errorText(cause)}`);
    }
  }, [reload]);

  return {
    widgets,
    kinds,
    kindOf,
    columns: data?.columns ?? 4,
    loaded: Boolean(data),
    error: data ? '' : error,
    loading,
    editing,
    saving,
    startEditing,
    cancel,
    save,
    add,
    remove,
    update,
    move,
    reset,
  };
}

export type Board = ReturnType<typeof useBoard>;
