import { Trash2, X } from 'lucide-react';
import { type CSSProperties, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { HomeWidget } from '../../../api';
import { Button } from '../../../components/Button';
import { type Board, isGroup } from './useBoard';
import { groupTitle } from './GroupSettings';
import { widgetInfo } from './widgets';

/** The gear that opens a widget's settings, found on the page (it is drawn by the grid, not here). */
export const gearOf = (id: string) => document.querySelector<HTMLElement>(`[data-settings-for="${CSS.escape(id)}"]`);

const WIDTH = 300;
const MARGIN = 8;

/** Where the pop-over sits: under its gear, right edges lined up; above it when there is no room below. */
function usePlace(id: string, card: React.RefObject<HTMLElement>) {
  const [style, setStyle] = useState<CSSProperties>({ visibility: 'hidden' });
  const [above, setAbove] = useState(false);
  useLayoutEffect(() => {
    let frame = 0;
    let first = true;
    const place = () => {
      frame = 0;
      const gear = gearOf(id);
      const element = card.current;
      if (!element) return;
      if (!gear) {
        // Not drawn yet (just added, or edit mode just began): try again next frame.
        frame = requestAnimationFrame(place);
        return;
      }
      let at = gear.getBoundingClientRect();
      if (first) {
        first = false;
        // A widget just added may sit below the window: bring it into view, then drop down from it.
        if (at.bottom < 0 || at.top > window.innerHeight) {
          gear.scrollIntoView({ block: 'center' });
          at = gear.getBoundingClientRect();
        }
      }
      // Its full height, before any cap from the last placing.
      element.style.maxHeight = 'none';
      const height = element.offsetHeight;
      const room = window.innerHeight;
      const below = room - at.bottom - 2 * MARGIN;
      const over = at.top - 2 * MARGIN;
      // Below the gear when it fits there, else above when it fits there, else on the roomier side,
      // as tall as that side allows (its settings then scroll inside it). Never over its own gear.
      const flip = height > below && (height <= over || over > below);
      const cap = Math.max(160, flip ? over : below);
      const shown = Math.min(height, cap);
      const width = Math.min(WIDTH, window.innerWidth - 2 * MARGIN);
      const left = Math.max(MARGIN, Math.min(window.innerWidth - width - MARGIN, at.right - width));
      const top = flip ? at.top - MARGIN - shown : at.bottom + MARGIN;
      element.style.maxHeight = '';
      setAbove(flip);
      setStyle({ top: Math.max(MARGIN, top), left, width, maxHeight: cap, visibility: 'visible' });
    };
    const later = () => {
      if (!frame) frame = requestAnimationFrame(place);
    };
    place();
    window.addEventListener('scroll', later, true);
    window.addEventListener('resize', later);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(later);
    if (card.current) observer?.observe(card.current);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', later, true);
      window.removeEventListener('resize', later);
      observer?.disconnect();
    };
  }, [id, card]);
  return { style, above };
}

/**
 * One widget's settings, in a small card that drops down from the gear that opened it: its own
 * title and title bar, what the widget itself offers, and whether a phone shows it.
 */
export function SettingsPopover({ board, widget, onClose }: { board: Board; widget: HomeWidget; onClose: () => void }) {
  const card = useRef<HTMLElement>(null);
  const { style, above } = usePlace(widget.id, card);
  const info = widgetInfo(widget.kind, board.kinds);
  const Settings = isGroup(widget) ? undefined : info.Settings;
  const group = isGroup(widget);
  const name = group ? groupTitle(widget) : widget.title || info.title;

  // Escape, or a press anywhere else (its own gear toggles it), closes it.
  useEffect(() => {
    const away = (event: PointerEvent) => {
      const target = event.target as Node;
      if (card.current?.contains(target) || gearOf(widget.id)?.contains(target)) return;
      // A menu or list the settings opened over the page (a place picker) is still part of them.
      if ((target as Element).closest?.('.board-popover-keep')) return;
      onClose();
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        gearOf(widget.id)?.focus();
      }
    };
    document.addEventListener('pointerdown', away, true);
    document.addEventListener('keydown', key, true);
    return () => {
      document.removeEventListener('pointerdown', away, true);
      document.removeEventListener('keydown', key, true);
    };
  }, [widget.id, onClose]);

  // The first field takes the focus once the card is placed (a hidden field cannot take it), so the
  // keyboard carries on where the gear was.
  const placed = style.visibility === 'visible';
  useEffect(() => {
    if (placed)
      card.current
        ?.querySelector<HTMLElement>('input, select, textarea, button:not(.board-popover-close)')
        ?.focus({ preventScroll: true });
  }, [placed, widget.id]);

  return createPortal(
    <section
      ref={card}
      className={`board-popover${above ? ' is-above' : ''}`}
      style={style}
      role="dialog"
      aria-label={`${name} settings`}
    >
      <header className="board-popover-head">
        <h3>{name}</h3>
        <button type="button" className="widget-tool board-popover-close" aria-label="Close" onClick={onClose}>
          <X />
        </button>
      </header>
      <div className="board-popover-body">
        {!group && (
          <>
            <label className="widget-setting">
              <span className="widget-setting-label">Title</span>
              <input
                className="widget-input"
                maxLength={40}
                value={widget.title ?? ''}
                placeholder={info.title}
                onChange={(event) => board.update(widget.id, { title: event.target.value })}
              />
            </label>
            <label className="board-panel-switch">
              <input
                type="checkbox"
                checked={!widget.hide_title}
                onChange={(event) => board.update(widget.id, { hide_title: !event.target.checked })}
              />
              <span>
                Show the title bar
                <small>Off: outside edit mode the widget shows only its contents.</small>
              </span>
            </label>
          </>
        )}
        {Settings && (
          <Settings
            widget={widget}
            onChange={(settings) => board.update(widget.id, { settings: settings as HomeWidget['settings'] })}
          />
        )}
        <label className="board-panel-switch">
          <input
            type="checkbox"
            checked={widget.hide_on_phone ?? false}
            onChange={(event) => board.update(widget.id, { hide_on_phone: event.target.checked })}
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
          onClick={() => {
            board.remove(widget.id);
            onClose();
          }}
        >
          {group ? 'Remove the group (its widgets stay)' : 'Remove from board'}
        </Button>
      </div>
    </section>,
    document.body,
  );
}
