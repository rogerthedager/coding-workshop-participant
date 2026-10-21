import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import IncidentTable from '../components/IncidentTable';
import { ErrorBox, Icon, Loading } from '../components/ui';
import { useLoad } from '../hooks';

const FILTERS = [
  ['', 'All'], ['Open', 'Open'], ['In Progress', 'In progress'], ['Blocked', 'Blocked'],
  ['Resolved', 'Resolved'], ['Closed', 'Closed'],
];

export default function MyTickets() {
  const [status, setStatus] = useState('');
  const [view, setView] = useState('mine');
  const [page, setPage] = useState(1);
  const summary = useLoad(() => api.employeeDashboard(), []);
  const list = useLoad(
    () => api.incidents({
      ...(view === 'mine' ? { scope: 'mine' } : { followed: true }),
      status: status || undefined,
      sort: 'updated',
      page,
    }),
    [status, page, view],
  );
  const counts = Object.fromEntries((summary.data?.status_counts || []).map((s) => [s.value, s.count]));
  const awaiting = summary.data?.awaiting_confirmation || [];
  const data = list.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My tickets</h1>
          <p>Incidents you reported or follow. Only you, the assigned engineer and admins can see what you report.</p>
        </div>
        <Link className="btn primary" to="/report"><Icon name="plus" size={18} stroke={2.2} />Report an incident</Link>
      </div>

      <div className="grid" style={{ '--min': '180px' }}>
        {[['Open', 'Open'], ['In Progress', 'In progress'], ['Blocked', 'Blocked']].map(([key, label]) => (
          <div className="card stat" key={key}>
            <div className="stat-label">{label}</div>
            <div className="big">{counts[key] ?? 0}</div>
          </div>
        ))}
        <div className="card stat good">
          <div className="stat-label">Awaiting your confirmation</div>
          <div className="big">{awaiting.length}</div>
        </div>
      </div>

      {awaiting.map((item) => (
        <div className="card banner ok" key={item.id}>
          <span style={{ color: 'var(--green)' }}><Icon name="check" size={22} /></span>
          <div style={{ flex: '1 1 300px', fontSize: 14 }}>
            <strong>{item.number} was marked resolved.</strong> Confirm the fix to close it, or reject it
            to send it back to the engineer.
            {item.seconds_until_auto_close != null && (
              <span className="muted"> It closes automatically {item.seconds_until_auto_close < 3600
                ? 'within the hour'
                : `in about ${Math.round(item.seconds_until_auto_close / 3600)} h`} if you do nothing.</span>
            )}
          </div>
          <Link className="btn" to={`/incidents/${item.id}`}>Review</Link>
        </div>
      ))}

      <section className="card" aria-labelledby="list-h">
        <div className="card-head">
          <div className="tabs" role="tablist" aria-label="Which tickets" style={{ margin: 0 }}>
            {[['mine', 'Reported by me'], ['followed', 'Following']].map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={view === key} className={view === key ? 'on' : ''}
                onClick={() => { setView(key); setPage(1); }}>
                <span id={key === view ? 'list-h' : undefined}>{label}</span>
              </button>
            ))}
          </div>
          <div className="chips" role="group" aria-label="Filter by status">
            {FILTERS.map(([key, label]) => (
              <button key={label} type="button" className={`fchip${status === key ? ' on' : ''}`}
                aria-pressed={status === key} onClick={() => { setStatus(key); setPage(1); }}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <ErrorBox error={list.error} />
        {list.loading && !data ? <Loading /> : data && (
          <>
            <IncidentTable items={data.items} />
            {data.items.length === 0 && (
              <div className="empty">
                {status ? 'No tickets with this status.'
                  : view === 'mine' ? 'You have not reported any incidents yet.'
                    : 'You are not following any tickets. Follow one from the suggestions on the report form.'}
              </div>
            )}
            <Pager data={data} page={page} pages={pages} setPage={setPage} />
          </>
        )}
      </section>
    </>
  );
}

export function Pager({ data, page, pages, setPage }) {
  const first = data.total ? (page - 1) * data.page_size + 1 : 0;
  const last = Math.min(data.total, page * data.page_size);
  return (
    <div className="table-foot">
      <span>{data.total ? `${first}–${last} of ${data.total}` : '0 tickets'}</span>
      <span className="actions">
        <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <button type="button" className="btn sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </span>
    </div>
  );
}
