import { createContext, type ReactNode, useContext, useLayoutEffect, useRef, useState } from 'react';

/** The room a widget's contents have on screen, in pixels. */
export interface Box {
  width: number;
  height: number;
}

/**
 * Every widget picks its layout from the room it really has (its body, under the title), not from
 * grid cells: the same four columns are wide on a big screen and narrow on a laptop. Set by the board
 * (and the Add list's preview); 0 × 0 before the first measure, which widgets treat as roomy.
 */
export const BoxContext = createContext<Box>({ width: 0, height: 0 });

export const useBox = () => useContext(BoxContext);

/** An element's size, followed as the window, the grid or a resize changes it. */
export function useMeasured<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [box, setBox] = useState<Box>({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const read = () => {
      const width = element.clientWidth;
      const height = element.clientHeight;
      setBox((last) => (last.width === width && last.height === height ? last : { width, height }));
    };
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(read);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, box] as const;
}

// Room kept under the last whole row for "+3 more".
const MORE = 22;

/**
 * A list that shows only the rows that fit whole, then "+N more": a widget never scrolls and never
 * shows a row cut in half. Rows can wrap (icons), and rows of different heights are measured as drawn.
 */
export function FitList({
  className,
  children,
  more,
  label,
}: {
  className?: string;
  /** The rows: one <li> each. */
  children: ReactNode;
  /** What stands in for the rows left out: a link to the app, or plain words. None: they just go. */
  more?: (hidden: number) => ReactNode;
  label?: string;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [hidden, setHidden] = useState(0);
  // After every render (the rows may have changed) and whenever the room changes.
  useLayoutEffect(() => {
    const outer = frame.current;
    const inner = list.current;
    if (!outer || !inner) return;
    const fit = () => {
      const rows = [...inner.children] as HTMLElement[];
      for (const row of rows) row.hidden = false;
      const room = outer.clientHeight;
      const bottom = (row: HTMLElement) => row.offsetTop + row.offsetHeight;
      let shown = rows.length;
      const reserve = more ? MORE : 0;
      if (rows.length && bottom(rows[rows.length - 1]) > room)
        shown = rows.findIndex((row) => bottom(row) > room - reserve);
      rows.forEach((row, index) => {
        row.hidden = index >= shown;
      });
      setHidden(rows.length - shown);
    };
    fit();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(fit);
    observer.observe(outer);
    return () => observer.disconnect();
  });
  return (
    // While "+N more" shows, the rows keep out of its line: rows that share out the height (weather's
    // days) would otherwise grow into it once the others are hidden.
    <div ref={frame} className={`fit${hidden > 0 && more ? ' has-more' : ''}`}>
      <ul ref={list} className={className} aria-label={label}>
        {children}
      </ul>
      {hidden > 0 && more && <div className="fit-more">{more(hidden)}</div>}
    </div>
  );
}
