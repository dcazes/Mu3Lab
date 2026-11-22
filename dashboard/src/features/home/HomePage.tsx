import { ArrowRight, Check, Pencil, Plus, X } from 'lucide-react';
import { useState } from 'react';
import { Button } from '../../components/Button';
import { Link } from '../../lib/router';
import { useDashboard } from '../../state/dashboard';
import { BoardView, type Panel, usePanelRoom } from './board/Board';
import { useBoard } from './board/useBoard';
import { CoreSetupProgress } from './CoreSetupProgress';
import { GetStarted } from './GetStarted';
import { SystemStatus } from './SystemStatus';
import { useHomeNotices } from './warnings';

function greeting() {
  const hour = new Date().getHours();
  return hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

function Attention() {
  const { notices, progress, dismiss } = useHomeNotices();
  const items = [...notices, ...progress];
  if (!items.length) return null;
  return (
    <section className="attention" aria-label="Needs attention">
      {items.map(({ key, warnings, icon: Icon, tone, text, more, to }) => (
        <div key={key} className="attention-row">
          <Link to={to} className={`attention-item attention-${tone}`}>
            <Icon className={tone === 'progress' ? 'spin' : ''} />
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
    </section>
  );
}

// On a computer the add list opens beside the board; on a phone it would cover it, so it waits to be asked for.
const wide = () => typeof window.matchMedia !== 'function' || window.matchMedia('(min-width: 1001px)').matches;

export function HomePage() {
  const { data } = useDashboard();
  const board = useBoard();
  const [panel, setPanel] = useState<Panel>('closed');
  const { home, shift, covered } = usePanelRoom(board.editing && panel !== 'closed');
  const cancel = () => {
    board.cancel();
    setPanel('closed');
  };
  const done = () => void board.save().then((saved) => saved && setPanel('closed'));
  const name = (data.identity.display_name || data.identity.username || '').split(' ')[0];
  return (
    <div className="home" ref={home} style={shift ? { transform: `translateX(-${shift}px)` } : undefined}>
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
          {board.editing ? (
            <>
              <Button variant="secondary" size="sm" icon={Plus} onClick={() => setPanel('add')}>
                Add widget
              </Button>
              <Button variant="ghost" size="sm" onClick={cancel}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" icon={Check} loading={board.saving} onClick={done}>
                Done
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              icon={Pencil}
              disabled={!board.loaded}
              onClick={() => {
                board.startEditing();
                setPanel(wide() ? 'add' : 'closed');
              }}
            >
              Edit
            </Button>
          )}
        </div>
      </header>
      <Attention />
      <CoreSetupProgress />
      <GetStarted />
      <section aria-label="Your board">
        <BoardView board={board} panel={panel} setPanel={setPanel} finish={covered ? { cancel, done } : undefined} />
      </section>
    </div>
  );
}
