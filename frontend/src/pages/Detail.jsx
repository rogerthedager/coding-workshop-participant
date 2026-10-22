import { age, dateTime, relative, statusLabel } from '../format';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { useDirectory } from '../directory';
import { Availability, Icon, Loading, Priority, SlaChip, StatusChip, Toast } from '../components/ui';
import { useToast } from '../hooks';
import { lengthProblem } from '../lib';

const TRIAGE_REASON = {
  unrouted_group: 'No team owns this category',
  no_engineer: 'Nobody on the team was on duty',
  returned: 'Returned to triage by the team',
};
const CLOSE_KIND = { confirmed: 'confirmed by the reporter', admin: 'closed by an admin', auto: 'closed automatically 72 h after resolution', duplicate: 'closed as a duplicate' };
const NOTE_EDIT_MS = 10 * 60 * 1000;
/** Whether the note's edit window has closed, checked against the clock (click time). */
const editWindowClosed = (item) => Date.now() - new Date(item.created_at).getTime() >= NOTE_EDIT_MS;

/** Images on the ticket; each opens through a short-lived signed link. */
function Attachments({ incident, items, canUpload, onUploaded }) {
  const [urls, setUrls] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    Promise.all(items.map((item) => api.attachmentUrl(item.id).then((r) => [item.id, r.url], () => [item.id, null])))
      .then((pairs) => live && setUrls(Object.fromEntries(pairs)));
    return () => { live = false; };
  }, [items]);
  const upload = async (event) => {
    const files = [...event.target.files];
    event.target.value = '';
    setBusy(true);
    setError('');
    try {
      for (const file of files) await api.uploadAttachment(incident.id, file);
    } catch (err) {
      setError(err.detail || err.message);
    } finally {
      setBusy(false);
      onUploaded();
    }
  };
  if (!items.length && !canUpload) return null;
  return (
    <section className="card pad" aria-labelledby="att-h" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="split" style={{ alignItems: 'center' }}>
        <h2 id="att-h">Photos</h2>
        <span className="tiny muted">{items.length}/5</span>
      </div>
      {error && <div className="alert" role="alert">{error}</div>}
      <div className="thumbs">
        {items.map((item) => {
          const url = urls[item.id];
          const label = `Photo added ${dateTime(item.created_at)}${item.visibility === 'internal' ? ' (staff only)' : ''}`;
          if (!url) return <span key={item.id} className="thumb">Loading…</span>;
          // Browsers other than Safari cannot show HEIC, so offer it as a download.
          if (item.content_type === 'image/heic') {
            return <a key={item.id} className="thumb" href={url} target="_blank" rel="noopener noreferrer">Download HEIC photo</a>;
          }
          return (
            <a key={item.id} href={url} target="_blank" rel="noopener noreferrer" title={label}>
              <img className="thumb" src={url} alt={label} />
            </a>
          );
        })}
      </div>
      {canUpload && items.length < 5 && (
        <label className="small" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          Add photos (JPEG, PNG, WebP or HEIC, 5 MB each)
          {/* No `capture`: it disables multi-select; image/* still offers the camera on phones. */}
          <input type="file" accept="image/*" multiple disabled={busy} onChange={upload} />
        </label>
      )}
      {busy && <span className="small muted" role="status">Uploading…</span>}
    </section>
  );
}

const PRIORITIES = ['Low', 'Medium', 'High', 'Critical'];
const BLOCKED_REASONS = ['Waiting on parts', 'Waiting on employee', 'Vendor needed', 'Access needed', 'Other'];
// [min, max] lengths from the API request models (routers/incidents.py, coordination.py).
const LIMIT = {
  title: [5, 120], description: [10, 5000], note: [1, 5000], reason: [1, 500], why: [10, 500], number: [1, 200],
};

/** Names the API sent with the ticket, its notes and its timeline ({ userId: full_name }). */
const peopleOf = (item, noteList, timeline) => ({ ...item.people, ...noteList.people, ...timeline.people });

/** A text field with the API's length limits and a plain hint while it breaks them. */
function TextField({ name, label, value, onChange, limit, multiline, style, ...rest }) {
  const id = `f-${name}`;
  const hint = lengthProblem(value, limit);
  const Tag = multiline ? 'textarea' : 'input';
  return (
    <div className="field" style={style}>
      <label htmlFor={id}>{label}</label>
      <Tag id={id} className="input" value={value} onChange={onChange} maxLength={limit[1]}
        aria-describedby={hint ? `${id}-hint` : undefined} {...rest} />
      {hint && <span id={`${id}-hint`} className="tiny muted" style={{ fontWeight: 400 }}>{hint}</span>}
    </div>
  );
}
const FINAL = ['Closed', 'Cancelled'];
const STEPS = [
  ['Open', 'created_at'],
  ['In Progress', 'first_in_progress_at'],
  ['Resolved', 'resolved_at'],
  ['Closed', 'closed_at'],
];

function Steps({ incident }) {
  const order = { Open: 0, 'In Progress': 1, Blocked: 1, Resolved: 2, Closed: 3 };
  const current = order[incident.status] ?? 0;
  return (
    <ol className="steps">
      {STEPS.map(([label, field], index) => {
        const done = index < current || incident.status === 'Closed';
        const state = done ? 'done' : index === current ? 'cur' : 'todo';
        const name = index === 1 && incident.status === 'Blocked' ? 'In progress (blocked)' : statusLabel(label);
        return (
          <li className="step" key={label}>
            <div className="step-line">
              <span className={`dot ${state}`}>{done ? '✓' : index + 1}</span>
              <span />
            </div>
            <div style={{ fontSize: 14, fontWeight: 500 }}>{name}</div>
            <div className="tiny muted">{incident[field] ? dateTime(incident[field]) : 'Not yet'}</div>
          </li>
        );
      })}
    </ol>
  );
}

function describe(event, person) {
  const p = event.payload || {};
  switch (event.type) {
    case 'created': return `Created · requested ${p.priority}`;
    case 'details_changed': return `Details changed (${(p.fields || []).join(', ')})`;
    case 'status_changed':
      if (p.close_kind === 'auto') return 'Closed automatically 72 h after resolution';
      if (p.close_kind === 'duplicate') return `Closed as a duplicate of ${p.duplicate_of_number}`;
      return `Status: ${statusLabel(p.from)} → ${statusLabel(p.to)}${p.to === 'Blocked' && p.reason ? ` (${p.reason.toLowerCase()})` : ''}`;
    case 'assigned': return p.via === 'auto_route'
      ? `Routed automatically to ${person(p.assignee, 'an engineer')}`
      : `Assigned to ${person(p.assignee, 'an engineer')}`;
    case 'unassigned': return p.via === 'returned_to_triage' ? 'Returned to triage' : 'Engineer unassigned';
    case 'priority_changed': return `Priority: ${p.from} → ${p.to}`;
    case 'escalation_requested': return 'Escalation requested';
    case 'escalation_approved': return `Escalation approved${p.priority ? ` · priority ${p.priority}` : ''}`;
    case 'escalation_rejected': return 'Escalation rejected';
    case 'escalation_superseded': return 'Escalation closed with the ticket';
    case 'note_added': return p.visibility === 'internal' ? 'Internal note added' : 'Note added';
    case 'note_edited': return 'Note edited';
    case 'note_hidden': return 'Note hidden by an admin';
    case 'duplicate_marked': return `Marked as a duplicate of ${p.duplicate_of_number}`;
    case 'attachment_added': return p.visibility === 'internal' ? 'Photo added (staff only)' : 'Photo added';
    case 'auto_closed': return 'Closed automatically';
    default: return event.type;
  }
}

/** A small inline form for one workflow action. */
function ActionForm({ title, children, onCancel, onSubmit, submitLabel, tone = 'primary', busy, valid = true }) {
  const ref = useRef(null);
  // Move focus into the form when it opens.
  useEffect(() => {
    const form = ref.current;
    (form?.querySelector('input, select, textarea') || form?.querySelector('h2'))?.focus();
  }, []);
  return (
    <form ref={ref} className="card dialog" onSubmit={(e) => { e.preventDefault(); if (valid) onSubmit(); }}>
      <h2 style={{ width: '100%' }} tabIndex={-1}>{title}</h2>
      {children}
      <div className="actions" style={{ marginLeft: 'auto' }}>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button type="submit" className={`btn ${tone}`} disabled={busy || !valid}>{submitLabel}</button>
      </div>
    </form>
  );
}

export default function Detail() {
  const { id } = useParams();
  const dir = useDirectory();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [attachments, setAttachments] = useState([]);
  // Names from the API, for people the directory cannot list (it has users for admins only).
  const [names, setNames] = useState({});
  const [suggestions, setSuggestions] = useState(null);
  const [editing, setEditing] = useState(null);
  // Current time for the 10-minute note edit window; ticks while the page is open.
  const [now, setNow] = useState(() => Date.now());
  const [incident, setIncident] = useState(null);
  const [notes, setNotes] = useState([]);
  const [events, setEvents] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [error, setError] = useState('');
  const [toast, showToast, setToast] = useToast();
  const [dialog, setDialog] = useState(null);
  const [fields, setFields] = useState({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState({ body: '', visibility: 'public' });

  const fetchAll = useCallback(
    () => Promise.all([
      api.incident(id),
      api.notes(id),
      api.timeline(id),
      api.attachments(id).catch(() => ({ items: [] })),
    ]).then((results) => [...results, Date.now()]),
    [id],
  );
  const apply = ([item, noteList, timeline, files, at]) => {
    setIncident(item);
    setNames(peopleOf(item, noteList, timeline));
    setNotes(noteList.items);
    setEvents(timeline.items);
    setAttachments(files.items);
    setNow(at);
    setLoadError(null);
  };
  const load = () => fetchAll().then(apply, setLoadError);
  // After a change that took away the caller's access, leave instead of showing "not found".
  const reloadAfter = (message) => fetchAll().then(apply, (err) => {
    if (err.status === 404) navigate('/', { state: { notice: `${message} You no longer have access to that ticket.` } });
    else setLoadError(err);
  });

  useEffect(() => {
    let live = true;
    fetchAll().then(
      ([item, noteList, timeline, files, at]) => {
        if (!live) return;
        setIncident(item);
        setNames(peopleOf(item, noteList, timeline));
        setNotes(noteList.items);
        setEvents(timeline.items);
        setAttachments(files.items);
        setNow(at);
        setLoadError(null);
      },
      (err) => live && setLoadError(err),
    );
    return () => {
      live = false;
    };
  }, [fetchAll]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000);
    return () => window.clearInterval(timer);
  }, []);

  if (loadError) {
    return (
      <div className="card pad">
        <h1 style={{ fontSize: 22 }}>{loadError.status === 404 ? 'Ticket not found' : 'Could not load this ticket'}</h1>
        <p className="muted">{loadError.status === 404 ? 'It does not exist, or you do not have access to it.' : loadError.message}</p>
        <Link to="/" className="btn">Back to my tickets</Link>
      </div>
    );
  }
  if (!incident) return <Loading />;

  const perm = incident.permissions;
  const final = FINAL.includes(incident.status);
  const esc = incident.latest_escalation;
  const pending = esc?.state === 'Pending';
  // The directory wins (and 'You' for the viewer), then names the API sent, then the fallback.
  const person = (userId, fallback) => dir.person(userId, names[userId]
    || (fallback ?? (userId === incident.assigned_to ? 'Assigned engineer' : 'Facilities team')));
  const field = (key, fallback = '') => fields[key] ?? fallback;
  const setField = (key) => (e) => setFields({ ...fields, [key]: e.target.value });
  const open = (name, initial = {}) => { setDialog(name); setFields(initial); setError(''); };
  const fits = (key, limit) => !lengthProblem(field(key), limit);

  /**
   * Run a mutation, then refresh everything (or go to `leaveTo` with the message); handles
   * version conflicts and Off-duty confirmation. Resolves to true on success, false on failure.
   */
  const run = async (action, message, { confirmRetry, leaveTo } = {}) => {
    setBusy(true);
    setError('');
    try {
      await action(incident);
      dir.reload();
      if (leaveTo) {
        navigate(leaveTo, { state: { notice: message } });
        return true;
      }
      setDialog(null);
      showToast(message);
      await reloadAfter(message);
      return true;
    } catch (err) {
      if (err.details?.confirmation_required && confirmRetry
        && window.confirm('This engineer is Off-duty. Assign anyway? They will see it next shift.')) {
        setBusy(false);
        return run(confirmRetry, message, { leaveTo });
      }
      if ((err.status === 409 && err.code === 'stale_version') || err.status === 428) {
        setError('Someone else updated this ticket. It has been reloaded; review it and try again.');
        await load();
      } else {
        setError(err.detail);
      }
      return false;
    } finally {
      setBusy(false);
    }
  };

  const transition = (body, message) => run((inc) => api.transition(inc, body), message);
  // Escalation decision: rejecting always needs the note; approving a Critical ticket needs it as the plan.
  const critical = incident.priority === 'Critical';
  const escNoteOk = fits('escNote', LIMIT.reason);
  const engineers = dir.engineers.filter((e) => e.is_active);
  const higher = PRIORITIES.filter((p) => PRIORITIES.indexOf(p) > PRIORITIES.indexOf(incident.priority));
  const canEdit = !final && (perm.staff || (perm.creator && incident.status === 'Open'));
  // Creators may cancel any Open ticket, even one auto-routing already assigned.
  const canCancel = incident.status === 'Open' && (perm.admin || perm.creator);
  const isAssignee = Boolean(user && incident.assigned_to === user.id && perm.staff);
  const canReturn = incident.status === 'Open' && incident.assigned_to && (isAssignee || perm.team_lead);
  const followerOnly = perm.follower && !perm.creator && !perm.staff_read;
  const canFollow = !final && !perm.creator && !perm.staff_read && !perm.follower;
  const canNote = perm.staff || perm.creator;
  const canUpload = !final && (perm.staff || perm.creator);
  const resolveAndClose = perm.creator && perm.staff && incident.status === 'In Progress';

  const follow = () => run(() => api.follow(incident.id), 'You are following this ticket.');
  const unfollow = () => run(() => api.unfollow(incident.id), 'You stopped following this ticket.');
  const openAssign = () => {
    open('assign', { assignee: incident.assigned_to || '' });
    setSuggestions(null);
    api.suggestions(incident.id).then((r) => setSuggestions(r.items), () => setSuggestions([]));
  };
  const markDuplicate = async () => {
    const number = field('number').trim();
    if (!number || !field('reason').trim()) {
      setError('Enter the ticket number and a reason.');
      return;
    }
    const found = await api.incidents({ q: number, page_size: 5 }).catch(() => ({ items: [] }));
    const target = found.items.find((item) => item.number.toLowerCase() === number.toLowerCase()
      || item.number.replace(/^INC-0*/i, '') === number.replace(/^(INC-?)?0*/i, ''));
    if (!target) {
      setError(`No ticket ${number} found.`);
      return;
    }
    run((inc) => api.markDuplicate(inc, { duplicate_of_id: target.id, reason: field('reason').trim() }),
      `Closed as a duplicate of ${target.number}. The reporter now follows it.`);
  };
  const canEditNote = (item) => !final && !item.hidden && user && item.author_id === user.id
    && now - new Date(item.created_at).getTime() < NOTE_EDIT_MS;
  const startEdit = (item) => {
    // Re-check at click time; the window may have closed since the last tick.
    if (editWindowClosed(item)) {
      setNow(new Date(item.created_at).getTime() + NOTE_EDIT_MS);
      setError('Notes can only be edited for 10 minutes after posting.');
      return;
    }
    setEditing({ id: item.id, body: item.body });
  };
  // Keep the editor open with its text if the save fails.
  const saveNote = async (item) => {
    if (await run(() => api.editNote(item, editing.body.trim()), 'Note updated.')) setEditing(null);
  };

  const postNote = async (event) => {
    event.preventDefault();
    if (!note.body.trim()) return;
    const ok = await run((inc) => api.addNote(inc, { body: note.body.trim(), visibility: perm.staff ? note.visibility : 'public' }), 'Note posted.');
    if (ok) setNote({ body: '', visibility: note.visibility });
  };
  const hideNote = async (item) => {
    const reason = window.prompt('Why hide this note? (visible to admins only)');
    if (!reason?.trim()) return;
    run(() => api.hideNote(item, reason.trim()), 'Note hidden.');
  };

  return (
    <>
      <Link to="/" className="back"><Icon name="back" size={16} />Back to tickets</Link>

      <div className="page-head" style={{ alignItems: 'flex-start' }}>
        <div style={{ flex: '1 1 420px', minWidth: 0 }}>
          <div className="actions" style={{ alignItems: 'center', gap: 10 }}>
            <span className="mono muted">{incident.number}</span>
            <StatusChip status={incident.status} />
            <Priority value={incident.priority} suffix=" priority" />
            <SlaChip state={incident.sla_state} />
            {perm.admin && incident.triage_reason && <span className="chip c-esc">{TRIAGE_REASON[incident.triage_reason]}</span>}
            {perm.follower && <span className="chip c-blk">Following</span>}
          </div>
          <h1 style={{ marginTop: 8 }}>{incident.title}</h1>
        </div>
        <div className="actions">
          {canEdit && <button type="button" className="btn" onClick={() => open('edit', { title: incident.title, description: incident.description })}>Edit details</button>}
          {perm.staff && incident.status === 'Open' && incident.assigned_to && (
            <button type="button" className="btn primary" disabled={busy} onClick={() => transition({ status: 'In Progress' }, 'Work started.')}>Start work</button>
          )}
          {perm.staff && incident.status === 'In Progress' && (
            <>
              <button type="button" className="btn" onClick={() => open('block', { reason: BLOCKED_REASONS[0], visibility: 'public' })}>Block</button>
              <button type="button" className="btn primary" onClick={() => open('resolve')}>Resolve</button>
              {resolveAndClose && <button type="button" className="btn ok" onClick={() => open('resolve', { close: true })}>Resolve and close</button>}
            </>
          )}
          {canReturn && <button type="button" className="btn" onClick={() => open('return')}>Return to triage</button>}
          {perm.staff && incident.status === 'Blocked' && (
            <button type="button" className="btn primary" disabled={busy} onClick={() => transition({ status: 'In Progress' }, 'Work resumed.')}>Resume work</button>
          )}
          {(perm.creator || perm.admin) && incident.status === 'Resolved' && (
            <>
              <button type="button" className="btn danger" onClick={() => open('reject')}>Not fixed</button>
              <button type="button" className="btn ok" disabled={busy} onClick={() => transition({ status: 'Closed' }, 'Ticket closed. Thanks for confirming.')}>Confirm fix</button>
            </>
          )}
          {perm.admin && !final && (
            <>
              <button type="button" className="btn" onClick={openAssign}>{incident.assigned_to ? 'Reassign' : 'Assign'}</button>
              <button type="button" className="btn" onClick={() => open('priority', { priority: incident.priority })}>Change priority</button>
              <button type="button" className="btn" onClick={() => open('duplicate', { number: '' })}>Mark duplicate</button>
            </>
          )}
          {canFollow && <button type="button" className="btn" disabled={busy} onClick={follow}>Follow</button>}
          {perm.follower && <button type="button" className="btn" disabled={busy} onClick={unfollow}>Unfollow</button>}
          {perm.creator && !final && (pending
            ? <button type="button" className="btn" disabled>Escalation pending</button>
            : <button type="button" className="btn" onClick={() => open('escalate')}>Request escalation</button>)}
          {canCancel && <button type="button" className="btn danger" onClick={() => open('cancel')}>Cancel ticket</button>}
        </div>
      </div>

      <Toast toast={toast} onDone={setToast} />
      {error && <div className="alert" role="alert">{error}</div>}

      {dialog === 'edit' && (
        <ActionForm title="Edit details" busy={busy} submitLabel="Save" onCancel={() => setDialog(null)}
          valid={fits('title', LIMIT.title) && fits('description', LIMIT.description)}
          onSubmit={() => run((inc) => api.updateIncident(inc, { title: field('title').trim(), description: field('description').trim() }), 'Details saved.')}>
          <TextField name="title" label="Title" style={{ width: '100%' }} limit={LIMIT.title}
            value={field('title')} onChange={setField('title')} />
          <TextField name="description" label="Description" style={{ width: '100%' }} limit={LIMIT.description} multiline
            value={field('description')} onChange={setField('description')} />
        </ActionForm>
      )}
      {dialog === 'block' && (
        <ActionForm title="Block this ticket" busy={busy} submitLabel="Block" onCancel={() => setDialog(null)}
          valid={fits('note', LIMIT.note)}
          onSubmit={() => transition({ status: 'Blocked', reason: field('reason'), note: field('note').trim(), visibility: field('visibility') }, 'Ticket blocked.')}>
          <label className="field">Reason
            <select className="input" value={field('reason')} onChange={setField('reason')}>
              {BLOCKED_REASONS.map((r) => <option key={r}>{r}</option>)}
            </select>
          </label>
          <TextField name="note" label="Note (required)" style={{ flex: '1 1 280px' }} limit={LIMIT.note}
            value={field('note')} onChange={setField('note')} placeholder="What's blocking it and what happens next" />
          <div className="seg" role="group" aria-label="Note visibility">
            {['public', 'internal'].map((v) => (
              <button key={v} type="button" className={field('visibility') === v ? 'on' : ''} onClick={() => setFields({ ...fields, visibility: v })}>
                {v === 'public' ? 'Public' : 'Internal · staff only'}
              </button>
            ))}
          </div>
        </ActionForm>
      )}
      {dialog === 'resolve' && (
        <ActionForm title={field('close') ? 'Resolve and close' : 'Resolve'} busy={busy}
          submitLabel={field('close') ? 'Resolve and close' : 'Mark resolved'} tone="ok" onCancel={() => setDialog(null)}
          valid={fits('note', LIMIT.note)}
          onSubmit={() => (field('close')
            // You reported it yourself, so confirm straight after resolving (FR-2.6).
            ? run(async (inc) => {
              const resolved = await api.transition(inc, { status: 'Resolved', note: field('note').trim(), visibility: 'public' });
              try {
                return await api.transition(resolved, { status: 'Closed' });
              } catch (err) {
                // The resolve went through; reload so the page shows it.
                await load();
                setDialog(null);
                throw err;
              }
            }, 'Resolved and closed.')
            : transition({ status: 'Resolved', note: field('note').trim(), visibility: 'public' }, 'Marked resolved. The reporter will confirm the fix.'))}>
          <TextField name="note" label="Public note for the reporter (required)" style={{ flex: '1 1 100%' }} limit={LIMIT.note} multiline
            value={field('note')} onChange={setField('note')} placeholder="What was fixed" />
        </ActionForm>
      )}
      {dialog === 'reject' && (
        <ActionForm title="It's not fixed" busy={busy} submitLabel="Send back to engineer" tone="danger" onCancel={() => setDialog(null)}
          valid={fits('note', LIMIT.note)}
          onSubmit={() => transition({ status: 'In Progress', note: field('note').trim(), visibility: 'public' }, 'Sent back to the engineer.')}>
          <TextField name="note" label="What's still wrong? (required)" style={{ flex: '1 1 100%' }} limit={LIMIT.note} multiline
            value={field('note')} onChange={setField('note')} />
        </ActionForm>
      )}
      {dialog === 'return' && (
        <ActionForm title="Return to triage" busy={busy} submitLabel="Return to triage" onCancel={() => setDialog(null)}
          valid={fits('reason', LIMIT.why)}
          // The caller usually loses access once it is unassigned, so leave the page.
          onSubmit={() => run((inc) => api.returnToTriage(inc, field('reason').trim()), 'Returned to triage. An admin will reassign it.',
            { leaveTo: perm.team_lead && !isAssignee ? '/team' : '/queue' })}>
          <TextField name="reason" label="Why doesn't this belong with the team? (10–500 characters)" style={{ flex: '1 1 100%' }}
            limit={LIMIT.why} multiline value={field('reason')} onChange={setField('reason')}
            placeholder="e.g. This is a door lock, not a network problem" />
        </ActionForm>
      )}
      {dialog === 'duplicate' && (
        <ActionForm title="Mark as duplicate" busy={busy} submitLabel="Close as duplicate" tone="danger" onCancel={() => setDialog(null)}
          valid={fits('number', LIMIT.number) && fits('reason', LIMIT.reason)} onSubmit={markDuplicate}>
          <TextField name="number" label="Duplicate of (ticket number)" limit={LIMIT.number}
            value={field('number')} onChange={setField('number')} placeholder="INC-000123" aria-required="true" />
          <TextField name="reason" label="Reason (required, staff only)" style={{ flex: '1 1 260px' }} limit={LIMIT.reason}
            value={field('reason')} onChange={setField('reason')} aria-required="true" />
          <p className="small muted" style={{ width: '100%', margin: 0 }}>This ticket closes, and its reporter starts following the other ticket.</p>
        </ActionForm>
      )}
      {dialog === 'cancel' && (
        <ActionForm title="Cancel this ticket" busy={busy} submitLabel="Cancel ticket" tone="danger" onCancel={() => setDialog(null)}
          valid={fits('reason', LIMIT.reason)}
          onSubmit={() => transition({ status: 'Cancelled', reason: field('reason').trim() }, 'Ticket cancelled.')}>
          <TextField name="reason" label="Reason (required)" style={{ flex: '1 1 100%' }} limit={LIMIT.reason}
            value={field('reason')} onChange={setField('reason')} placeholder="e.g. Reported twice, or it fixed itself" />
        </ActionForm>
      )}
      {dialog === 'assign' && (
        <ActionForm title={incident.assigned_to ? 'Reassign' : 'Assign an engineer'} busy={busy} submitLabel="Assign" onCancel={() => setDialog(null)}
          // A different engineer (or Unassign), plus a reason when someone already has it.
          valid={(field('assignee') || null) !== (incident.assigned_to || null) && (!incident.assigned_to || fits('reason', LIMIT.reason))}
          onSubmit={() => {
            const body = { assigned_to: field('assignee') || null, reason: field('reason').trim() || undefined };
            run((inc) => api.assign(inc, body), 'Assignment saved.', { confirmRetry: (inc) => api.assign(inc, { ...body, confirm_unavailable: true }) });
          }}>
          {suggestions === null ? <p className="small muted" style={{ width: '100%' }}>Ranking engineers…</p> : (
            <div role="radiogroup" aria-label="Engineer, best match first" style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 320, overflowY: 'auto' }}>
              {incident.status === 'Open' && incident.assigned_to && (
                <label className="suggest"><input type="radio" name="assignee" checked={!field('assignee')} onChange={() => setFields({ ...fields, assignee: '' })} />Unassign</label>
              )}
              {(suggestions.length ? suggestions : engineers.map((e) => ({
                id: e.user_id, full_name: e.user.full_name, availability: e.availability, open_count: e.workload, skills: e.skills, match_reasons: [],
              }))).map((s, index, all) => (
                <div key={s.id}>
                  {index > 0 && all[index - 1].skill_match && !s.skill_match && (
                    <div className="tiny muted" style={{ borderTop: '1px solid var(--line)', margin: '6px 0', paddingTop: 6 }}>Without a matching skill</div>
                  )}
                  <label className="suggest" style={{ cursor: 'pointer' }}>
                    <input type="radio" name="assignee" value={s.id} checked={field('assignee') === s.id}
                      onChange={() => setFields({ ...fields, assignee: s.id })} />
                    <span style={{ flex: '1 1 160px', fontWeight: 500 }}>{s.full_name}{s.id === incident.assigned_to ? ' (current)' : ''}</span>
                    <Availability value={s.availability} />
                    <span className="small muted">{s.open_count} open</span>
                    {s.match_reasons.includes('skill') && <span className="chip sla-on-track">Skill match</span>}
                    {s.match_reasons.includes('home_building') && <span className="chip c-blk">Home building</span>}
                  </label>
                </div>
              ))}
            </div>
          )}
          {incident.assigned_to && (
            <TextField name="reason" label="Reason (required)" style={{ flex: '1 1 260px' }} limit={LIMIT.reason}
              value={field('reason')} onChange={setField('reason')} />
          )}
        </ActionForm>
      )}
      {dialog === 'priority' && (
        <ActionForm title="Change priority" busy={busy} submitLabel="Save" onCancel={() => setDialog(null)}
          valid={fits('reason', LIMIT.reason)}
          onSubmit={() => run((inc) => api.setPriority(inc, { priority: field('priority'), reason: field('reason').trim() }), 'Priority changed.')}>
          <label className="field">Priority
            <select className="input" value={field('priority')} onChange={setField('priority')}>
              {PRIORITIES.slice().reverse().map((p) => <option key={p}>{p}</option>)}
            </select>
          </label>
          <TextField name="reason" label="Reason (required)" style={{ flex: '1 1 260px' }} limit={LIMIT.reason}
            value={field('reason')} onChange={setField('reason')} />
        </ActionForm>
      )}
      {dialog === 'escalate' && (
        <ActionForm title="Request escalation" busy={busy} submitLabel="Send request" onCancel={() => setDialog(null)}
          valid={fits('reason', LIMIT.why)}
          onSubmit={() => run((inc) => api.escalate(inc, field('reason').trim()), 'Escalation requested. An admin will review it.')}>
          <TextField name="reason" label="Why does this need more urgency? (10–500 characters)" style={{ flex: '1 1 100%' }}
            limit={LIMIT.why} multiline value={field('reason')} onChange={setField('reason')} />
        </ActionForm>
      )}

      <section className="card pad" aria-label="Progress">
        {incident.status === 'Cancelled'
          ? <div className="muted">Cancelled {dateTime(incident.cancelled_at)}.</div>
          : <Steps incident={incident} />}
        {incident.status === 'Blocked' && (
          <div className="blockbar">
            <Icon name="stop" size={18} />
            <strong>Blocked · {incident.blocked_reason}</strong>
            <span style={{ color: 'var(--blocked-ink)' }}>since {dateTime(incident.blocked_since)} ({age(incident.blocked_since)})</span>
          </div>
        )}
      </section>

      {esc && (pending || esc.decided_at) && (
        <section className="card pad banner warn" style={{ display: 'block' }}>
          <div className="actions" style={{ alignItems: 'flex-start', gap: 12 }}>
            <span style={{ color: 'var(--warn-ink)' }}><Icon name="up" size={22} /></span>
            <div style={{ flex: '1 1 360px' }}>
              <h2 style={{ color: 'var(--warn-ink-2)' }}>
                {pending
                  ? `Escalation requested by ${person(esc.requester_id)} · ${age(esc.requested_at)} ago`
                  : `Escalation ${esc.state.toLowerCase()} · requested by ${person(esc.requester_id)} ${age(esc.requested_at)} ago`}
              </h2>
              <p style={{ margin: '6px 0 0', fontSize: 14 }}>“{esc.reason}”</p>
              {!pending && (esc.decision_reason || esc.action_plan) && (
                <p style={{ margin: '8px 0 0', fontSize: 14 }}><strong>Decision:</strong> {esc.decision_reason || esc.action_plan}</p>
              )}
              {pending && perm.creator && !perm.admin && (
                <p className="small muted" style={{ margin: '8px 0 0' }}>An admin will review your request. You can request again 24 hours after a decision.</p>
              )}
            </div>
          </div>
          {pending && perm.admin && (
            <div className="filters" style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--warn-rule)' }}>
              {incident.priority !== 'Critical' && (
                <label>New priority
                  <select className="input" style={{ minWidth: 160 }} value={field('escPriority', higher[0])} onChange={setField('escPriority')}>
                    {higher.map((p) => <option key={p}>{p}</option>)}
                  </select>
                </label>
              )}
              <div style={{ flex: '1 1 280px', display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label>
                  {critical ? 'Action plan (required to approve) or rejection reason' : 'Public note (required to reject)'}
                  <input className="input" value={field('escNote')} onChange={setField('escNote')} maxLength={LIMIT.reason[1]}
                    placeholder="e.g. Part expedited; arriving 9 AM" aria-describedby={escNoteOk ? undefined : 'esc-hint'} />
                </label>
                {!escNoteOk && (
                  <span id="esc-hint" className="tiny muted">
                    {lengthProblem(field('escNote'), LIMIT.reason)}{field('escNote').trim() ? '' : (critical ? ' to approve or reject' : ' to reject')}
                  </span>
                )}
              </div>
              <div className="actions">
                <button type="button" className="btn danger" disabled={busy || !escNoteOk}
                  onClick={() => run((inc) => api.decideEscalation(inc, { approved: false, decision_reason: field('escNote').trim() }), 'Escalation rejected.')}>
                  Reject
                </button>
                <button type="button" className="btn ok" disabled={busy || (critical && !escNoteOk)}
                  onClick={() => run((inc) => api.decideEscalation(inc, {
                    approved: true,
                    priority: incident.priority === 'Critical' ? undefined : field('escPriority', higher[0]),
                    action_plan: field('escNote').trim() || undefined,
                  }), 'Escalation approved.')}>
                  Approve
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      <div className="row">
        <div className="grow">
          <section className="card pad">
            <h2 style={{ marginBottom: 8 }}>Description</h2>
            {followerOnly
              ? <p className="small muted" style={{ margin: 0 }}>You're following this ticket. The full description is visible to the reporter and the facilities team; you get its public updates.</p>
              : <p style={{ margin: 0, fontSize: 15, lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>{incident.description}</p>}
            <div className="facts">
              <div><div className="lbl">Category</div><div className="val">{dir.category(incident.category_id)}</div></div>
              <div><div className="lbl">Location</div><div className="val">{dir.location(incident, ' › ')}</div></div>
              {incident.requested_priority && <div><div className="lbl">Requested priority</div><div className="val">{incident.requested_priority}</div></div>}
              <div><div className="lbl">Actual priority</div><div className="val">{incident.priority}</div></div>
              <div><div className="lbl">Assignee</div><div className="val">{incident.assigned_to ? person(incident.assigned_to, 'Assigned engineer') : 'Unassigned'}</div></div>
              <div><div className="lbl">Reported</div><div className="val">{incident.created_by ? `${person(incident.created_by)} · ` : ''}{dateTime(incident.created_at)}</div></div>
              {!final && !incident.acknowledged_at && (
                <div><div className="lbl">Acknowledge by</div><div className="val">{dateTime(incident.ack_due_at)} ({relative(incident.ack_due_at)})</div></div>
              )}
              {!final && incident.status !== 'Resolved' && (
                <div><div className="lbl">Resolve by</div><div className="val">{dateTime(incident.resolve_due_at)} ({relative(incident.resolve_due_at)})</div></div>
              )}
              {incident.close_kind && <div><div className="lbl">Closed</div><div className="val">{CLOSE_KIND[incident.close_kind]}</div></div>}
              {incident.route_return_count > 0 && <div><div className="lbl">Returned to triage</div><div className="val">{incident.route_return_count}×</div></div>}
            </div>
          </section>

          <Attachments incident={incident} items={attachments} canUpload={canUpload} onUploaded={load} />

          <section className="card pad" aria-labelledby="notes-h" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="split" style={{ alignItems: 'center' }}>
              <h2 id="notes-h">Notes</h2>
              {!perm.staff_read && <span className="tiny muted">Showing public notes</span>}
            </div>
            {notes.length === 0 && <p className="small muted" style={{ margin: 0 }}>No notes yet.</p>}
            {notes.map((item) => item.hidden ? (
              <div key={item.id} className="note hidden">Note by {person(item.author_id)} hidden by an admin · {dateTime(item.created_at)}</div>
            ) : (
              <article key={item.id} className={`note${item.visibility === 'internal' ? ' internal' : ''}`}>
                <div className="note-head">
                  <strong style={{ fontSize: 14 }}>{person(item.author_id)}</strong>
                  <span className={`tag ${item.visibility === 'internal' ? 'int' : 'pub'}`}>{item.visibility === 'internal' ? 'Internal' : 'Public'}</span>
                  <span className="tiny muted">{dateTime(item.created_at)}{item.edited_at ? ' · edited' : ''}</span>
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 12 }}>
                    {canEditNote(item) && editing?.id !== item.id && (
                      <button type="button" className="linkbtn" onClick={() => startEdit(item)}>Edit</button>
                    )}
                    {perm.admin && <button type="button" className="linkbtn" onClick={() => hideNote(item)}>Hide</button>}
                  </span>
                </div>
                {editing?.id === item.id ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <label className="sr-only" htmlFor={`edit-${item.id}`}>Edit note</label>
                    <textarea id={`edit-${item.id}`} className="input" rows={3} maxLength={5000} value={editing.body}
                      onChange={(e) => setEditing({ ...editing, body: e.target.value })} />
                    <div className="actions">
                      <button type="button" className="btn sm" onClick={() => setEditing(null)}>Cancel</button>
                      <button type="button" className="btn sm primary" disabled={busy || !editing.body.trim()} onClick={() => saveNote(item)}>Save</button>
                    </div>
                    <span className="tiny muted">Notes can be edited for 10 minutes after posting.</span>
                  </div>
                ) : <p>{item.body}</p>}
              </article>
            ))}
            {!final && canNote && (
              <form onSubmit={postNote} style={{ borderTop: '1px solid var(--line-2)', paddingTop: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
                <label htmlFor="note-body" className="small" style={{ fontWeight: 500 }}>Add a note</label>
                <textarea id="note-body" className="input" rows={3} placeholder="Write an update…" maxLength={5000}
                  value={note.body} onChange={(e) => setNote({ ...note, body: e.target.value })} />
                <div className="split" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
                  {perm.staff ? (
                    <div className="seg" role="group" aria-label="Note visibility">
                      {['public', 'internal'].map((v) => (
                        <button key={v} type="button" className={note.visibility === v ? 'on' : ''} aria-pressed={note.visibility === v}
                          onClick={() => setNote({ ...note, visibility: v })}>
                          {v === 'public' ? 'Public' : 'Internal · staff only'}
                        </button>
                      ))}
                    </div>
                  ) : <span className="small muted">Visible to you, the engineer and admins.</span>}
                  <button type="submit" className="btn primary" disabled={busy || !note.body.trim()}>Post note</button>
                </div>
              </form>
            )}
          </section>
        </div>

        <aside className="card pad side" aria-labelledby="tl-h">
          <h2 id="tl-h" style={{ marginBottom: 8 }}>Timeline</h2>
          <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {events.map((event) => {
              const internal = event.payload?.visibility === 'internal';
              return (
                <li className="ev" key={event.id}>
                  <span className={`evdot${internal ? ' staff' : event.type === 'created' ? ' sys' : ''}`} aria-hidden="true" />
                  <div>
                    <div style={{ fontSize: 14 }}>{describe(event, person)}</div>
                    <div className="tiny muted" style={{ marginTop: 2 }}>
                      {event.actor_id ? `${person(event.actor_id)} · ` : ''}{dateTime(event.created_at)}{internal ? ' · staff only' : ''}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        </aside>
      </div>
    </>
  );
}
