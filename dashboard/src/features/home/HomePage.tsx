import { ArrowRight, Check, LayoutGrid, Pencil, Plus, Redo2, Undo2, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '../../components/Button';
import { Link } from '../../lib/router';
import { jobQueue, JobQueueNotice } from '../apps/JobQueue';
import { useDashboard } from '../../state/dashboard';
import { BoardView, type Panel, usePanelRoom } from './board/Board';
import { BoardTabs } from './board/BoardTabs';
import { type Board, useBoard } from './board/useBoard';
import { CoreSetupProgress } from './CoreSetupProgress';
import { GetStarted } from './GetStarted';
import { SystemStatus } from './SystemStatus';
import { useHomeNotices } from './warnings';

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

function Attention() {
  const { notices, working, dismiss } = useHomeNotices();
  const queue = jobQueue(working);
  if (!notices.length && !queue.now.length && !queue.waiting.length) return null;
  return (
    <section className="attention" aria-label="Needs attention">
      {notices.map(({ key, warnings, icon: Icon, tone, text, more, to }) => (
        <div key={key} className="attention-row">
          <Link to={to} className={`attention-item attention-${tone}`}>
            <Icon />
            <span>
              {text}
              {more > 0 && <small className="attention-more"> +{more} more</small>}
            </span>
            <ArrowRight className="attention-arrow" />
          </Link>
          {warnings.length > 0 && (
            <button
              type="button"
              className="btn btn-ghost btn-sm btn-icon"
              aria-label="Hide this warning until it changes"
              title="Hide until it changes"
              onClick={() => dismiss(warnings)}
            >
              <X />
            </button>
          )}
        </div>
      ))}
      <JobQueueNotice services={working} />
    </section>
  );
}

/**
 * Edit mode's toolbar, kept in view while scrolling: how the board is laid out (columns, free or
 * gravity), undo and redo, then adding widgets and finishing.
 */
function EditBar({
  board,
  onAdd,
  onCancel,
  onDone,
}: {
  board: Board;
  onAdd: () => void;
  onCancel: () => void;
  onDone: () => void;
}) {
  const { undo, redo } = board;
  // Ctrl+Z undoes and Ctrl+Shift+Z (or Ctrl+Y) redoes, except while typing in a field.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing =
        event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable]');
      if (!(event.ctrlKey || event.metaKey) || typing) return;
      const key = event.key.toLowerCase();
      if (key === 'z' && !event.shiftKey) undo();
      else if ((key === 'z' && event.shiftKey) || key === 'y') redo();
      else return;
      event.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [undo, redo]);
  return (
    <div className="board-editbar" role="toolbar" aria-label="Arrange your board">
      <label className="board-columns">
        Columns
        <select
          className="select-sm"
          value={board.columns}
          onChange={(event) => board.setColumns(Number(event.target.value))}
          aria-label="Columns across the board"
        >
          {board.columnChoices.map((count) => (
            <option key={count} value={count}>
              {count}
            </option>
          ))}
        </select>
      </label>
      <div className="segmented" role="radiogroup" aria-label="How widgets sit">
        {(
          [
            ['free', 'Free', 'Widgets stay where you put them; ones pushed aside slide back'],
            ['gravity', 'Gravity', 'Widgets always pack up to the top'],
          ] as const
        ).map(([value, label, hint]) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={board.placement === value}
            title={hint}
            onClick={() => board.setPlacement(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {board.placement === 'free' && (
        <Button
          variant="ghost"
          size="sm"
          icon={LayoutGrid}
          title="Move every widget up as far as it goes, closing the gaps"
          onClick={board.tidy}
        >
          Tidy up
        </Button>
      )}
      <span className="board-editbar-history">
        <Button
          variant="ghost"
          size="sm"
          icon={Undo2}
          className="btn-icon"
          aria-label="Undo"
          title="Undo (Ctrl+Z)"
          disabled={!board.canUndo}
          onClick={undo}
        />
        <Button
          variant="ghost"
          size="sm"
          icon={Redo2}
          className="btn-icon"
          aria-label="Redo"
          title="Redo (Ctrl+Shift+Z)"
          disabled={!board.canRedo}
          onClick={redo}
        />
      </span>
      <span className="board-editbar-end">
        <Button variant="secondary" size="sm" icon={Plus} onClick={onAdd}>
          Add widget
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" size="sm" icon={Check} loading={board.saving} onClick={onDone}>
          Done
        </Button>
      </span>
    </div>
  );
}

export function HomePage() {
  const { data } = useDashboard();
  const board = useBoard();
  const [panel, setPanel] = useState<Panel>('closed');
  const { home, covered } = usePanelRoom(board.editing && panel === 'add');
  const cancel = () => {
    board.cancel();
    setPanel('closed');
  };
  const done = () => void board.save().then((saved) => saved && setPanel('closed'));
  const name = (data.identity.display_name || data.identity.username || '').split(' ')[0];
  return (
    <div className="home" ref={home}>
      <header className="home-header">
        <div>
          <h1>
            {greeting()}
            {name ? `, ${name}` : ''}
          </h1>
          <p>{new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</p>
        </div>
        <SystemStatus />
        <div className="home-edit">
          {!board.editing && (
            <Button
              variant="ghost"
              size="sm"
              icon={Pencil}
              disabled={!board.loaded}
              onClick={() => {
                // Edit only arranges the board; Add widget opens the list.
                board.startEditing();
                setPanel('closed');
              }}
            >
              Edit
            </Button>
          )}
        </div>
      </header>
      {board.editing && <EditBar board={board} onAdd={() => setPanel('add')} onCancel={cancel} onDone={done} />}
      <Attention />
      <CoreSetupProgress />
      <GetStarted />
      <BoardTabs board={board} />
      <section aria-label="Your board">
        <BoardView board={board} panel={panel} setPanel={setPanel} finish={covered ? { cancel, done } : undefined} />
      </section>
    </div>
  );
}
