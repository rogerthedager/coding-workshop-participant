import { age } from '../format';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useDirectory } from '../directory';
import { useClientPage, useLoad } from '../hooks';
import { Availability, ErrorBox, Loading, Priority, StatusChip } from '../components/ui';
import { assignWithConfirm, byUrgency } from '../lib';
import { Pager } from './MyTickets';

const ACTIVE = ['Open', 'In Progress', 'Blocked', 'Resolved'];
/** Items per page in each list. */
export const QUEUE_PAGE_SIZE = 10;
export const TEAM_PAGE_SIZE = 20;
const FOOT = { borderTop: '1px solid var(--line-2)' };

/** Admin workload board: balance load, hand out unassigned tickets, clear blockers. */
export default function Team() {
  const dir = useDirectory();
  const [focus, setFocus] = useState(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState(null);
  const tickets = useLoad(() => api.incidents({ status: ACTIVE, page_size: 100, sort: 'updated' }), []);
  const engineers = dir.engineers.filter((e) => e.is_active);
  const items = tickets.data?.items || [];
  const unassigned = items.filter((t) => !t.assigned_to && t.status === 'Open').sort(byUrgency);
  const assigned = items.filter((t) => t.assigned_to);
  const blocked = assigned.filter((t) => t.status === 'Blocked');
  const rows = assigned.filter((t) => !focus || t.assigned_to === focus);
  const maxLoad = Math.max(5, ...engineers.map((e) => e.workload));
  const unassignedPage = useClientPage(unassigned, QUEUE_PAGE_SIZE);
  const blockedPage = useClientPage(blocked, QUEUE_PAGE_SIZE);
  const rowsPage = useClientPage(rows, TEAM_PAGE_SIZE);
  const focusOn = (userId) => {
    setFocus(userId);
    rowsPage.reset();
  };

  const assign = async (incident, userId) => {
    if (!userId) return;
    setError(null);
    try {
      const result = await assignWithConfirm(incident, userId);
      if (!result) return;
      const engineer = dir.engineerByUser(userId);
      setNotice(`Assigned ${incident.number} to ${engineer?.user.full_name}${engineer?.availability === 'Off-duty' ? ' (off duty, they’ll see it next shift)' : ''}`);
      tickets.reload();
      dir.reload();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Workload</h1>
          <p>Balance the load, hand out new tickets and clear blockers.</p>
        </div>
        {notice && <div className="notice" role="status">{notice}</div>}
      </div>
      <ErrorBox error={error || tickets.error} />

      <div className="grid" style={{ '--min': '220px' }}>
        {engineers.map((e) => {
          const on = focus === e.user_id;
          return (
            <button key={e.id} type="button" className={`eng${on ? ' on' : ''}`} aria-pressed={on}
              onClick={() => focusOn(on ? null : e.user_id)}>
              <span className="split" style={{ width: '100%', alignItems: 'center' }}>
                <span style={{ fontSize: 15, fontWeight: 500 }}>{e.user.full_name}</span>
                <Availability value={e.availability} />
              </span>
              <span style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                <span className="big" style={{ fontSize: 28 }}>{e.workload}</span>
                <span className="small muted">active tickets</span>
              </span>
              <span className="meter"><span style={{ width: `${Math.round((e.workload / maxLoad) * 100)}%` }} /></span>
              <span className="small" style={{ color: 'var(--ink-2)' }}>
                {e.status_counts.Open || 0} open · {e.status_counts['In Progress'] || 0} in progress · {e.status_counts.Blocked || 0} blocked
              </span>
              {e.skills.length > 0 && (
                <span className="chips" style={{ gap: 4 }}>{e.skills.map((s) => <span key={s} className="skill">{s}</span>)}</span>
              )}
            </button>
          );
        })}
      </div>

      <div className="grid" style={{ '--min': '420px' }}>
        <section className="card" aria-labelledby="un-h">
          <div className="card-head" style={{ paddingBottom: 8 }}>
            <h2 id="un-h">Waiting for an engineer</h2>
            <span className="small muted">{unassigned.length} {unassigned.length === 1 ? 'ticket' : 'tickets'}</span>
          </div>
          {tickets.loading && !tickets.data && <Loading />}
          {unassignedPage.shown.map((t) => (
            <div className="list-row" key={t.id}>
              <div className="main">
                <div style={{ fontSize: 14, fontWeight: 500 }}>{t.title}</div>
                <div className="small muted" style={{ marginTop: 2 }}>
                  <Link className="mono" to={`/incidents/${t.id}`}>{t.number}</Link> · {dir.location(t)} · {age(t.created_at)}
                </div>
              </div>
              <Priority value={t.priority} />
              <label className="sr-only" htmlFor={`assign-${t.id}`}>Assign {t.number} to</label>
              <select id={`assign-${t.id}`} className="sel" value="" onChange={(e) => assign(t, e.target.value)}>
                <option value="">Assign to…</option>
                {engineers.map((e) => <option key={e.user_id} value={e.user_id}>{e.user.full_name} · {e.availability}</option>)}
              </select>
            </div>
          ))}
          {tickets.data && unassigned.length === 0 && (
            <div className="empty" style={{ borderTop: '1px solid var(--line-2)' }}>Every open ticket has an engineer.</div>
          )}
          {unassignedPage.pages > 1 && <div style={FOOT}><Pager {...unassignedPage.pagerProps} /></div>}
        </section>

        <section className="card" aria-labelledby="blk-h">
          <div className="card-head" style={{ paddingBottom: 8 }}><h2 id="blk-h">Blocked</h2></div>
          {blockedPage.shown.map((t) => (
            <div className="list-row" key={t.id}>
              <div className="main">
                <div style={{ fontSize: 14, fontWeight: 500 }}>{t.title}</div>
                <div className="small muted" style={{ marginTop: 2 }}>
                  <span className="mono">{t.number}</span> · {dir.person(t.assigned_to, t.people?.[t.assigned_to] || 'Engineer')} · {t.blocked_reason}
                </div>
              </div>
              <span className="small nowrap" style={{ color: 'var(--red)' }}>Blocked {age(t.blocked_since)}</span>
              <Link className="btn sm" to={`/incidents/${t.id}`}>Open</Link>
            </div>
          ))}
          {tickets.data && blocked.length === 0 && <div className="empty" style={{ borderTop: '1px solid var(--line-2)' }}>Nothing is blocked.</div>}
          {blockedPage.pages > 1 && <div style={FOOT}><Pager {...blockedPage.pagerProps} /></div>}
        </section>
      </div>

      <section className="card" aria-labelledby="tt-h">
        <div className="card-head" style={{ paddingBottom: 8 }}>
          <h2 id="tt-h">{focus ? `${dir.person(focus)}’s tickets` : 'All team tickets'}</h2>
          {focus && <button type="button" className="btn sm" onClick={() => focusOn(null)}>Show whole team</button>}
        </div>
        <div className="scroll">
          <table>
            <thead><tr><th>Number</th><th>Title</th><th>Engineer</th><th>Status</th><th>Priority</th><th>Age</th></tr></thead>
            <tbody>
              {rowsPage.shown.map((t) => (
                <tr key={t.id}>
                  <td><Link className="mono" to={`/incidents/${t.id}`}>{t.number}</Link></td>
                  <td className="t-title">{t.title}</td>
                  <td className="nowrap">{dir.person(t.assigned_to, t.people?.[t.assigned_to] || 'Engineer')}</td>
                  <td><StatusChip status={t.status} /></td>
                  <td><Priority value={t.priority} /></td>
                  <td className="muted nowrap">{age(t.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {tickets.data && rows.length === 0 && <div className="empty">No active tickets.</div>}
        {rowsPage.pages > 1 && <div style={FOOT}><Pager {...rowsPage.pagerProps} /></div>}
      </section>
    </>
  );
}
