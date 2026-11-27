import { ArrowLeft, ArrowRight, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import type { HomeBoardTab } from '../../../api';
import { useConfirm } from '../../../components/Dialog';
import { Menu } from '../../../components/Menu';
import type { Board } from './useBoard';

/**
 * The person's boards as tabs above Home. Outside edit mode they only switch boards (and show at all
 * only once there are two). While arranging, the board on screen can be renamed right in its tab,
 * moved left or right and removed, and "New board" adds an empty one.
 */
export function BoardTabs({ board }: { board: Board }) {
  const confirm = useConfirm();
  const { tabs, tab, editing } = board;
  // The name being typed for the board on screen; saved when the field is left.
  const [naming, setNaming] = useState<string | null>(null);
  if (!board.loaded || (!editing && tabs.length < 2)) return null;

  const save = (next: HomeBoardTab[]) => void board.saveTabs(next);
  const index = tabs.findIndex((item) => item.id === tab);
  const rename = () => {
    if (naming === null) return;
    const name = naming.trim();
    setNaming(null);
    if (name && name !== tabs[index]?.name) save(tabs.map((item) => (item.id === tab ? { ...item, name } : item)));
  };
  const move = (by: number) => {
    const next = [...tabs];
    const [moved] = next.splice(index, 1);
    next.splice(index + by, 0, moved);
    save(next);
  };
  const remove = async () => {
    const current = tabs[index];
    if (
      !current ||
      !(await confirm({
        title: `Remove “${current.name}”?`,
        description: 'Its widgets go with it. Your other boards stay as they are.',
        confirmLabel: 'Remove board',
        tone: 'danger',
      }))
    )
      return;
    save(tabs.filter((item) => item.id !== tab));
  };
  const add = async () => {
    const result = await board.saveTabs([...tabs, { name: `Board ${tabs.length + 1}` }]);
    const made = result?.[result.length - 1];
    if (made?.id) await board.switchTab(made.id);
  };

  return (
    <nav className="board-tabs" aria-label="Your boards">
      <div className="board-tabs-list" role="tablist">
        {tabs.map((item) => {
          const shown = item.id === tab;
          if (shown && editing)
            return (
              <span key={item.id} className="board-tab is-current is-editing" role="tab" aria-selected>
                <input
                  className="board-tab-name"
                  aria-label="Board name"
                  maxLength={30}
                  value={naming ?? item.name}
                  size={Math.max(4, (naming ?? item.name).length + 1)}
                  onChange={(event) => setNaming(event.target.value)}
                  onBlur={rename}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') event.currentTarget.blur();
                    if (event.key === 'Escape') setNaming(null);
                  }}
                />
                <Menu
                  compact
                  label={`${item.name} actions`}
                  items={[
                    ...(index > 0 ? [{ label: 'Move left', icon: ArrowLeft, onSelect: () => move(-1) }] : []),
                    ...(index < tabs.length - 1
                      ? [{ label: 'Move right', icon: ArrowRight, onSelect: () => move(1) }]
                      : []),
                    ...(tabs.length > 1
                      ? [{ label: 'Remove board', icon: Trash2, danger: true, onSelect: () => void remove() }]
                      : []),
                  ]}
                />
              </span>
            );
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={shown}
              className={`board-tab${shown ? ' is-current' : ''}`}
              disabled={board.saving}
              title={editing && !shown ? 'Saves this board, then arranges that one' : undefined}
              onClick={() => void board.switchTab(item.id ?? '')}
            >
              {item.name}
            </button>
          );
        })}
      </div>
      {editing && tabs.length < 12 && (
        <button type="button" className="board-tab board-tab-add" onClick={() => void add()}>
          <Plus aria-hidden />
          New board
        </button>
      )}
    </nav>
  );
}
