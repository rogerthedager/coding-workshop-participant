import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { useDirectory } from '../directory';
import IncidentTable from '../components/IncidentTable';
import { ErrorBox, Loading } from '../components/ui';
import { useLoad } from '../hooks';
import { statusLabel } from '../format';
import { Pager } from './MyTickets';

const STATUSES = ['Open', 'In Progress', 'Blocked', 'Resolved', 'Closed', 'Cancelled'];
const PRIORITIES = ['Critical', 'High', 'Medium', 'Low'];
const SLA = [['on_track', 'On track'], ['at_risk', 'At risk'], ['breached', 'Breached']];
// Filters set by dashboard links that have no control on this page, shown as removable chips.
const LINK_FILTERS = {
  team_id: 'Team', routed_team_id: 'Routed to team', triage_reason: 'Triage reason',
  category_group_id: 'Category group', category_id: 'Category', floor_id: 'Floor',
  seat_id: 'Seat', assigned_to: 'Assignee', resolved_from: 'Resolved from', resolved_to: 'Resolved before',
  created_from: 'Created from', created_to: 'Created before', date_from: 'Created from', date_to: 'Created until',
  resolved_by: 'Resolved by', public_note: 'No public note', escalation: 'Escalation', created_by: 'Reporter',
};

/**
 * Generic ticket list driven by the URL, so dashboard links such as
 * /incidents?status=Blocked&building_id=… open the matching list.
 */
export default function IncidentList({ preset = {}, title = 'Search', intro }) {
  const { isStaff } = useAuth();
  const dir = useDirectory();
  const [params, setParams] = useSearchParams();
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(null);
  // The page number lives in the URL so back/forward and shared links keep it.
  const page = Math.max(1, Number(params.get('page')) || 1);
  const query = { ...preset };
  for (const key of new Set(params.keys())) {
    if (key === 'page') continue;
    const values = params.getAll(key);
    query[key] = values.length > 1 ? values : values[0];
  }
  const key = JSON.stringify(query);
  const list = useLoad(() => api.incidents({ ...JSON.parse(key), page }), [key, page]);
  const filterNames = [...new Set(params.keys())].filter((name) => name !== 'page');
  const statuses = params.getAll('status');
  const building = params.get('building_id') || '';
  const buildings = dir.buildings.filter((b) => b.is_active !== false);

  const set = (name, value) => {
    const next = new URLSearchParams(params);
    next.delete(name);
    for (const item of [].concat(value || [])) next.append(name, item);
    next.delete('page');
    setParams(next);
  };
  const setPage = (number) => {
    const next = new URLSearchParams(params);
    if (number > 1) next.set('page', number); else next.delete('page');
    setParams(next);
  };
  const toggleStatus = (status) =>
    set('status', statuses.includes(status) ? statuses.filter((s) => s !== status) : [...statuses, status]);
  const exportCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      await api.exportIncidents(JSON.parse(key));
    } catch (err) {
      setExportError(err);
    } finally {
      setExporting(false);
    }
  };
  const data = list.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          <p>{intro || (params.get('q') ? `Results for “${params.get('q')}”.` : 'Every ticket you are allowed to see.')}</p>
        </div>
        <button type="button" className="btn" onClick={exportCsv} disabled={exporting}>
          {exporting ? 'Exporting…' : 'Export CSV'}
        </button>
      </div>
      <ErrorBox error={exportError} />
      <section className="card">
        <div className="card-head filters">
          <fieldset style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <legend className="small" style={{ fontSize: 13, marginBottom: 4 }}>Status{statuses.length ? '' : ' (any)'}</legend>
            <div className="chips">
              {STATUSES.map((s) => (
                <button key={s} type="button" className={`fchip${statuses.includes(s) ? ' on' : ''}`}
                  aria-pressed={statuses.includes(s)} onClick={() => toggleStatus(s)}>
                  {statusLabel(s)}
                </button>
              ))}
            </div>
          </fieldset>
          <label>Priority
            <select className="sel" value={params.get('priority') || ''} onChange={(e) => set('priority', e.target.value)}>
              <option value="">Any</option>
              {PRIORITIES.map((p) => <option key={p}>{p}</option>)}
            </select>
          </label>
          <label>SLA
            <select className="sel" value={params.get('sla_state') || ''} onChange={(e) => set('sla_state', e.target.value)}>
              <option value="">Any</option>
              {SLA.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label>Building
            <select className="sel" value={building} onChange={(e) => set('building_id', e.target.value)}>
              <option value="">All buildings</option>
              {buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              {/* A link may name a building that is no longer listed (e.g. inactive). */}
              {building && !buildings.some((b) => b.id === building) && <option value={building}>Selected building</option>}
            </select>
          </label>
          <label>Sort
            <select className="sel" value={params.get('sort') || preset.sort || 'created'} onChange={(e) => {
              const next = new URLSearchParams(params);
              next.set('sort', e.target.value);
              // Next due first is ascending; the others are newest or most urgent (Critical) first.
              // Always explicit, so a preset direction (e.g. /queue's asc) cannot flip them.
              next.set('direction', e.target.value === 'sla_due' ? 'asc' : 'desc');
              next.delete('page');
              setParams(next);
            }}>
              <option value="created">Newest</option>
              <option value="updated">Recently updated</option>
              <option value="priority">Priority</option>
              <option value="sla_due">SLA due soonest</option>
            </select>
          </label>
          <label style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <input type="checkbox" checked={params.get('followed') === 'true'} onChange={(e) => set('followed', e.target.checked ? 'true' : '')} />
            Only tickets I follow
          </label>
          {filterNames.length > 0 && (
            <button type="button" className="linkbtn" onClick={() => setParams({})}>Clear filters</button>
          )}
        </div>
        {[...params.keys()].some((name) => LINK_FILTERS[name]) && (
          <div className="chips" style={{ padding: '0 20px 12px' }} aria-label="Other filters">
            {[...new Set(params.keys())].filter((name) => LINK_FILTERS[name]).map((name) => {
              const raw = params.getAll(name).join(', ');
              const value = name === 'category_id' ? dir.category(raw)
                : name === 'assigned_to' && raw !== 'unassigned' ? dir.person(raw, 'Engineer')
                  : /_(from|to)$/.test(name) ? new Date(raw).toLocaleDateString()
                    : name.endsWith('_id') || name === 'resolved_by' ? '' : raw;
              return (
                <button key={name} type="button" className="fchip on" onClick={() => set(name, '')}
                  aria-label={`Remove filter ${LINK_FILTERS[name]}`}>
                  {LINK_FILTERS[name]}{value ? `: ${value}` : ''} ×
                </button>
              );
            })}
          </div>
        )}
        <ErrorBox error={list.error} />
        {list.loading && !data ? <Loading /> : data && (
          <>
            <IncidentTable items={data.items} assigneeFallback={isStaff ? 'Engineer' : 'Assigned'} />
            {data.items.length === 0 && <div className="empty">No tickets match these filters.</div>}
            <Pager data={data} page={page} pages={pages} setPage={setPage} />
          </>
        )}
      </section>
    </>
  );
}
