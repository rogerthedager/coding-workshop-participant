import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { useDirectory } from '../directory';
import { initials, statusLabel } from '../format';
import { useLoad, useToast } from '../hooks';
import { ErrorBox, Loading, Priority, SlaChip } from '../components/ui';
import './Board.css';

const ACTIVE = ['Open', 'In Progress', 'Blocked', 'Resolved'];
const FINAL = ['Closed', 'Cancelled'];
const BLOCKED_REASONS = ['Waiting on parts', 'Waiting on employee', 'Vendor needed', 'Access needed', 'Other'];

/**
 * Client-side copy of the backend VALID_TRANSITIONS table, limited to the moves the board offers
 * (Open → Cancelled needs a reason and lives on the detail page). `kind` selects the input form.
 */
const MOVES = [
  { from: 'Open', to: 'In Progress', kind: 'start', who: 'staff' },
  { from: 'In Progress', to: 'Blocked', kind: 'block', who: 'staff' },
  { from: 'Blocked', to: 'In Progress', kind: 'resume', who: 'staff' },
  { from: 'In Progress', to: 'Resolved', kind: 'resolve', who: 'staff' },
  { from: 'Resolved', to: 'In Progress', kind: 'reject', who: 'creator' },
  { from: 'Resolved', to: 'Closed', kind: 'close', who: 'creator', final: true },
];

const NEEDS_FORM = new Set(['block', 'resolve', 'reject']);

/** Moves this caller may make on this ticket right now. */
function allowedMoves(incident, showFinal) {
  const perm = incident.permissions || {};
  return MOVES.filter((move) => {
    if (move.from !== incident.status) return false;
    if (move.final && !showFinal) return false;
    if (move.who === 'staff' && !perm.staff) return false;
    if (move.who === 'creator' && !(perm.creator || perm.admin)) return false;
    if (move.kind === 'start' && !incident.assigned_to) return false;
    return true;
  });
}

const SUCCESS = {
  start: (n) => `${n} moved to In progress.`,
  block: (n) => `${n} blocked.`,
  resume: (n) => `${n} back in progress.`,
  resolve: (n) => `${n} marked resolved. The reporter will confirm the fix.`,
  reject: (n) => `${n} sent back to the engineer.`,
  close: (n) => `${n} closed.`,
};

const NARROW = '(max-width: 899px)';
function subscribeNarrow(callback) {
  const query = window.matchMedia(NARROW);
  query.addEventListener('change', callback);
  return () => query.removeEventListener('change', callback);
}
const useNarrow = () => useSyncExternalStore(subscribeNarrow, () => window.matchMedia(NARROW).matches, () => false);

function Card({ incident, moves, dir, selfName, onMove, onDragStart, onDragEnd, dragging }) {
  const readOnly = FINAL.includes(incident.status);
  const assignee = incident.assigned_to
    ? dir.person(incident.assigned_to, incident.people?.[incident.assigned_to] || 'Assigned engineer') : null;
  const selectId = `move-${incident.id}`;
  return (
    <li
      className={`card board-card${dragging ? ' dragging' : ''}`}
      draggable={!readOnly && moves.length > 0}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', incident.id);
        onDragStart(incident);
      }}
      onDragEnd={onDragEnd}
    >
      <div className="board-card-top">
        <Link to={`/incidents/${incident.id}`} className="mono">{incident.number}</Link>
        <Priority value={incident.priority} />
      </div>
      <p className="board-card-title">{incident.title}</p>
      <div className="board-card-meta">
        <SlaChip state={incident.sla_state} />
        <span className="tiny muted">{dir.location(incident)}</span>
      </div>
      <div className="board-card-foot">
        <span className={`avatar sm${assignee ? '' : ' gray'}`} title={assignee || 'Unassigned'} aria-hidden="true">
          {assignee ? initials(assignee === 'You' ? selfName : assignee) : '–'}
        </span>
        <span className="tiny muted board-assignee">{assignee || 'Unassigned'}</span>
        <span className="sr-only">Status: {statusLabel(incident.status)}</span>
        {!readOnly && moves.length > 0 && (
          <>
            <label htmlFor={selectId} className="sr-only">Move {incident.number} to</label>
            <select id={selectId} className="sel board-move" value="" onChange={(e) => e.target.value && onMove(incident, e.target.value)}>
              <option value="">Move to…</option>
              {moves.map((move) => (
                <option key={move.kind} value={move.kind}>
                  {move.kind === 'reject' ? 'In progress (not fixed)' : statusLabel(move.to)}
                </option>
              ))}
            </select>
          </>
        )}
      </div>
    </li>
  );
}

function MoveForm({ pending, busy, onCancel, onSubmit, setFields }) {
  const { incident, move, fields } = pending;
  const set = (key) => (e) => setFields({ ...fields, [key]: e.target.value });
  const titles = {
    block: `Block ${incident.number}`,
    resolve: `Resolve ${incident.number}`,
    reject: `${incident.number} is not fixed`,
  };
  const submit = { block: 'Block', resolve: 'Mark resolved', reject: 'Send back to engineer' };
  const tone = { block: 'primary', resolve: 'ok', reject: 'danger' };
  return (
    <form className="card dialog" aria-label={titles[move.kind]}
      onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>
      <h2 style={{ width: '100%' }}>{titles[move.kind]}</h2>
      <p className="small muted" style={{ width: '100%', margin: 0 }}>{incident.title}</p>
      {move.kind === 'block' && (
        <>
          <label className="field">Reason
            <select className="input" value={fields.reason} onChange={set('reason')} autoFocus>
              {BLOCKED_REASONS.map((r) => <option key={r}>{r}</option>)}
            </select>
          </label>
          <label className="field" style={{ flex: '1 1 280px' }}>Note (required)
            <input className="input" value={fields.note} onChange={set('note')} required
              placeholder="What's blocking it and what happens next" />
          </label>
          <div className="seg" role="group" aria-label="Note visibility">
            {['public', 'internal'].map((v) => (
              <button key={v} type="button" className={fields.visibility === v ? 'on' : ''} aria-pressed={fields.visibility === v}
                onClick={() => setFields({ ...fields, visibility: v })}>
                {v === 'public' ? 'Public' : 'Internal · staff only'}
              </button>
            ))}
          </div>
        </>
      )}
      {move.kind === 'resolve' && (
        <label className="field" style={{ flex: '1 1 100%' }}>Public note for the reporter (required)
          <textarea className="input" value={fields.note} onChange={set('note')} required autoFocus placeholder="What was fixed" />
        </label>
      )}
      {move.kind === 'reject' && (
        <label className="field" style={{ flex: '1 1 100%' }}>What's still wrong? (required)
          <textarea className="input" value={fields.note} onChange={set('note')} required autoFocus />
        </label>
      )}
      <div className="actions" style={{ marginLeft: 'auto' }}>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button type="submit" className={`btn ${tone[move.kind]}`} disabled={busy || !fields.note.trim()}>
          {submit[move.kind]}
        </button>
      </div>
    </form>
  );
}

export default function Board() {
  const { user, isAdmin, isEngineer } = useAuth();
  const dir = useDirectory();
  const narrow = useNarrow();
  const [showFinal, setShowFinal] = useState(false);
  const [tab, setTab] = useState('Open');
  const [dragged, setDragged] = useState(null);
  const [over, setOver] = useState(null);
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // { text, id }: a repeated message still re-renders, re-announces and gets its own timer.
  const [toast, showToast, setToast] = useToast();
  const formRef = useRef(null);

  const scope = isAdmin ? undefined : isEngineer ? 'assigned' : undefined;
  // Active and final tickets load separately so closed history never crowds out live work.
  const tickets = useLoad(async () => {
    const base = { page_size: 100, scope };
    const [active, done] = await Promise.all([
      api.incidents({ ...base, status: ACTIVE, sort: 'priority' }),
      showFinal ? api.incidents({ ...base, status: FINAL, sort: 'updated' }) : { items: [] },
    ]);
    return [...active.items, ...done.items];
  }, [showFinal, scope]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(timer);
  }, [toast, setToast]);

  useEffect(() => {
    if (pending) formRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [pending]);

  const columns = showFinal ? [...ACTIVE, ...FINAL] : ACTIVE;
  const activeTab = columns.includes(tab) ? tab : 'Open';
  const items = tickets.data || [];
  const byStatus = Object.fromEntries(columns.map((c) => [c, items.filter((i) => i.status === c)]));
  const dragTargets = dragged ? allowedMoves(dragged, showFinal).map((m) => m.to) : [];

  const run = async (incident, move, body) => {
    setBusy(true);
    setError('');
    try {
      await api.transition(incident, body);
      setPending(null);
      showToast(SUCCESS[move.kind](incident.number));
    } catch (err) {
      setPending(null);
      setError(`${incident.number}: ${err.detail || err.message} The board has been refreshed.`);
    } finally {
      setBusy(false);
      tickets.reload();
    }
  };

  /** Shared by drag-and-drop and the "Move to…" select. */
  const startMove = (incident, move) => {
    setError('');
    if (NEEDS_FORM.has(move.kind)) {
      setPending({
        incident,
        move,
        fields: { reason: BLOCKED_REASONS[0], note: '', visibility: 'public' },
      });
      return;
    }
    setPending(null);
    run(incident, move, { status: move.to });
  };

  const submitForm = () => {
    const { incident, move, fields } = pending;
    const note = fields.note.trim();
    const body = move.kind === 'block'
      ? { status: move.to, reason: fields.reason, note, visibility: fields.visibility }
      : { status: move.to, note, visibility: 'public' };
    run(incident, move, body);
  };

  const dropOn = (column) => {
    const incident = dragged;
    setDragged(null);
    setOver(null);
    if (!incident || busy) return;
    const move = allowedMoves(incident, showFinal).find((m) => m.to === column);
    if (move) startMove(incident, move);
  };

  const renderColumn = (column) => {
    const list = byStatus[column];
    const readOnly = FINAL.includes(column);
    const allowed = dragTargets.includes(column);
    const dimmed = dragged && !allowed && column !== dragged.status;
    const headingId = `col-${column.replace(/\s+/g, '-').toLowerCase()}`;
    return (
      <section
        key={column}
        aria-labelledby={headingId}
        className={`board-col${readOnly ? ' readonly' : ''}${dimmed ? ' dim' : ''}${allowed ? ' target' : ''}${over === column && allowed ? ' over' : ''}`}
        onDragOver={(e) => {
          if (!allowed) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
          if (over !== column) setOver(column);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget) && over === column) setOver(null);
        }}
        onDrop={(e) => {
          e.preventDefault();
          if (allowed) dropOn(column);
        }}
      >
        <h2 id={headingId} className="board-col-head">
          {statusLabel(column)} <span className="board-count">{list.length}</span>
          {readOnly && <span className="tiny muted"> · read-only</span>}
        </h2>
        {list.length === 0 ? (
          <p className="board-empty">No tickets</p>
        ) : (
          <ul className="board-list">
            {list.map((incident) => (
              <Card
                key={incident.id}
                incident={incident}
                moves={readOnly ? [] : allowedMoves(incident, showFinal)}
                dir={dir}
                selfName={user?.full_name}
                dragging={dragged?.id === incident.id}
                onDragStart={setDragged}
                onDragEnd={() => { setDragged(null); setOver(null); }}
                onMove={(inc, kind) => {
                  const move = allowedMoves(inc, showFinal).find((m) => m.kind === kind);
                  if (move) startMove(inc, move);
                }}
              />
            ))}
          </ul>
        )}
      </section>
    );
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Board</h1>
          <p>
            {isAdmin ? 'All live tickets' : 'Tickets assigned to you'} by status. Drag a card to another
            column, or use “Move to…” on the card.
          </p>
        </div>
        <label className="board-toggle">
          <input type="checkbox" checked={showFinal} onChange={(e) => setShowFinal(e.target.checked)} />
          Show closed and cancelled
        </label>
      </div>

      <div aria-live="polite" role="status" className="board-live">
        {toast && <div key={toast.id} className="toast">{toast.text}</div>}
      </div>
      {error && <div className="alert" role="alert">{error}</div>}
      <ErrorBox error={tickets.error} />

      {pending && (
        <div ref={formRef}>
          <MoveForm
            pending={pending}
            busy={busy}
            onCancel={() => setPending(null)}
            onSubmit={submitForm}
            setFields={(fields) => setPending({ ...pending, fields })}
          />
        </div>
      )}

      {!tickets.data && !tickets.error ? <Loading /> : tickets.data && (
        narrow ? (
          <div className="board-narrow">
            <div className="tabs" role="tablist" aria-label="Board columns">
              {columns.map((column) => (
                <button key={column} type="button" role="tab" aria-selected={activeTab === column}
                  className={activeTab === column ? 'on' : ''} onClick={() => setTab(column)}>
                  {statusLabel(column)} ({byStatus[column].length})
                </button>
              ))}
            </div>
            <div role="tabpanel">{renderColumn(activeTab)}</div>
          </div>
        ) : (
          <div className="board" style={{ '--cols': columns.length }} aria-busy={tickets.loading || busy}>
            {columns.map(renderColumn)}
          </div>
        )
      )}
    </>
  );
}
