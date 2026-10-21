import { age, initials } from '../format';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useDirectory } from '../directory';
import { useLoad, useToast } from '../hooks';
import { assignWithConfirm, byUrgency, engineerLabel } from '../lib';

const REASON = {
  unrouted_group: ['No owning team', 'c-blk'],
  no_engineer: ['Nobody on duty', 'c-esc'],
  returned: ['Returned by team', 'c-crit'],
};
const TOP_SUGGESTIONS = 30;
import { Availability, ErrorBox, Loading, Priority, Toast } from '../components/ui';

export default function Triage() {
  const dir = useDirectory();
  const list = useLoad(
    () => api.incidents({ status: 'Open', assigned_to: 'unassigned', page_size: 100, sort: 'priority' }),
    [],
  );
  const [choice, setChoice] = useState({});
  const [toast, showToast, setToast] = useToast();
  // Tickets with an assignment in flight, so a double click cannot assign twice.
  const [assigning, setAssigning] = useState({});
  const [error, setError] = useState(null);
  const engineers = dir.engineers.filter((e) => e.is_active);
  const rows = (list.data?.items || []).slice().sort(byUrgency);
  // Top-ranked engineer per ticket (skill, availability, workload, home building).
  const ids = rows.slice(0, TOP_SUGGESTIONS).map((r) => r.id);
  const tops = useLoad(async () => {
    const pairs = await Promise.all(ids.map((rowId) => api.suggestions(rowId)
      .then((r) => [rowId, r.items[0] || null], () => [rowId, null])));
    return Object.fromEntries(pairs);
  }, [ids]);
  const top = (incidentId) => tops.data?.[incidentId];

  const assign = async (incident) => {
    const userId = choice[incident.id] || top(incident.id)?.id || engineers[0]?.user_id;
    if (!userId || assigning[incident.id]) return;
    setError(null);
    setAssigning((current) => ({ ...current, [incident.id]: true }));
    try {
      const result = await assignWithConfirm(incident, userId);
      if (!result) return;
      showToast(`${incident.number} assigned to ${dir.person(userId)}. It moved out of triage.`);
      list.reload();
      dir.reload();
    } catch (err) {
      setError(err);
      list.reload();
    } finally {
      setAssigning((current) => ({ ...current, [incident.id]: false }));
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Triage</h1>
          <p>Tickets automatic routing could not place, highest priority first, then oldest. The suggested engineer is preselected.</p>
        </div>
        <span className="muted" style={{ fontSize: 14 }}>{rows.length} waiting</span>
      </div>
      <Toast toast={toast} onDone={setToast} />
      <ErrorBox error={error || list.error} />

      <div className="row">
        <section className="card grow" aria-label="Unassigned tickets" style={{ flexBasis: 640, gap: 0 }}>
          {list.loading && !list.data ? <Loading /> : (
            <div className="scroll">
              <table>
                <thead><tr><th>Priority</th><th>Number</th><th>Title</th><th>Why here</th><th>Location</th><th>Age</th><th>Assign to</th></tr></thead>
                <tbody>
                  {rows.map((incident) => (
                    <tr key={incident.id}>
                      <td><Priority value={incident.priority} /></td>
                      <td><Link className="mono" to={`/incidents/${incident.id}`}>{incident.number}</Link></td>
                      <td className="t-title">
                        {incident.title}
                        <div className="tiny muted" style={{ fontWeight: 400, marginTop: 2 }}>{dir.category(incident.category_id)}</div>
                      </td>
                      <td className="nowrap">
                        {incident.triage_reason
                          ? <span className={`chip ${REASON[incident.triage_reason][1]}`}>{REASON[incident.triage_reason][0]}</span>
                          : <span className="small muted">Unassigned</span>}
                        {top(incident.id) && (
                          <div className="tiny muted" style={{ marginTop: 4 }}>
                            Suggested: {top(incident.id).full_name}{top(incident.id).skill_match ? ' · skill match' : ''}
                          </div>
                        )}
                      </td>
                      <td className="muted nowrap">{dir.location(incident)}</td>
                      <td className="muted nowrap">{age(incident.created_at)}</td>
                      <td className="nowrap">
                        <div style={{ display: 'flex', gap: 8 }}>
                          <label className="sr-only" htmlFor={`eng-${incident.id}`}>Engineer for {incident.number}</label>
                          <select id={`eng-${incident.id}`} className="sel" value={choice[incident.id] || top(incident.id)?.id || ''}
                            onChange={(e) => setChoice({ ...choice, [incident.id]: e.target.value })}>
                            {engineers.map((e) => <option key={e.user_id} value={e.user_id}>{engineerLabel(e)}</option>)}
                          </select>
                          <button type="button" className="btn primary sm" style={{ minHeight: 40 }} disabled={!engineers.length || assigning[incident.id]}
                            onClick={() => assign(incident)}>{assigning[incident.id] ? 'Assigning…' : 'Assign'}</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {list.data && rows.length === 0 && <div className="empty">Nothing waiting for triage.</div>}
        </section>

        <aside className="card pad side" aria-labelledby="wl-h">
          <h2 id="wl-h">Engineer workload</h2>
          <p className="small muted" style={{ margin: '4px 0 12px' }}>Non-final tickets per engineer.</p>
          {engineers.map((e) => (
            <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0', borderTop: '1px solid var(--line-2)' }}>
              <div className="avatar gray">{initials(e.user.full_name)}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 500 }}>{e.user.full_name}</div>
                <div className="tiny muted">{e.workload} open · {e.status_counts.Blocked || 0} blocked{e.skills.length ? ` · ${e.skills.join(', ')}` : ''}</div>
              </div>
              <Availability value={e.availability} />
            </div>
          ))}
          {engineers.length === 0 && <p className="small muted">No active engineers yet.</p>}
          <p className="tiny muted" style={{ margin: '12px 0 0' }}>Off-duty engineers can still be assigned after a confirmation.</p>
        </aside>
      </div>
    </>
  );
}
