import { type LucideIcon, MoreHorizontal } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';

export interface MenuItem {
  label: string;
  icon?: LucideIcon;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}

export function Menu({ items, label = 'More actions' }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !root.current?.contains(event.target as Node))
        setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);
  if (!items.length) return null;
  return (
    <div className="menu" ref={root}>
      <button
        type="button"
        className="btn btn-secondary btn-md btn-icon"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        <MoreHorizontal />
      </button>
      {open && (
        <div className="menu-list" role="menu" id={id}>
          {items.map(({ label: itemLabel, icon: Icon, onSelect, danger, disabled }) => (
            <button
              key={itemLabel}
              type="button"
              role="menuitem"
              className={danger ? 'danger' : ''}
              disabled={disabled}
              onClick={() => {
                setOpen(false);
                onSelect();
              }}
            >
              {Icon && <Icon />}
              {itemLabel}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
