import { age, duration, pct, statusLabel, timeAgo } from '../format';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, appHref } from '../api';
import { useAuth } from '../auth';
import { useDirectory } from '../directory';
import { useLoad } from '../hooks';
import { cohortQuery } from '../lib';
import { ErrorBox, Loading, SlaChip } from '../components/ui';
import TeamSummary from './TeamSummary';
import './Dashboards.css';

const PERIODS = [['7', 'Last 7 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days']];

const TRIAGE_REASONS = {
  unrouted_group: 'No owning team',
  no_engineer: 'Nobody on duty',
  returned: 'Returned by team',
};
const triageReason = (reason) => TRIAGE_REASONS[reason] || 'Not routed';

/** App search link for filters the list API supports. */
function searchHref(filters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value == null || value === '') continue;
    for (const item of [].concat(value)) params.append(key, item);
  }
  return `/incidents?${params}`;
}

const HOTSPOT_LEVELS = [['buildings', 'Buildings'], ['floors', 'Floors'], ['seats', 'Seats']];

/**
 * Change vs the previous period as text, e.g. "+25 vs previous 30 days".
 * `better` is 'up' or 'down' to color the direction; omit it to stay neutral.
 */
export function Delta({ value, kind = 'count', better, period }) {
  const { change, previous } = value || {};
  if (change == null) {
    return <span className="delta">{previous == null ? `No data for previous ${period}` : `Previous ${period}: –`}</span>;
  }
  if (change === 0) return <span className="delta">No change vs previous {period}</span>;
  const abs = Math.abs(change);
  const text = kind === 'seconds' ? duration(abs) : kind === 'ratio' ? `${Math.round(abs * 100)} pts` : String(Math.round(abs * 100) / 100);
  const tone = better ? ((change > 0) === (better === 'up') ? 'is-good' : 'is-bad') : '';
  return <span className={`delta ${tone}`}>{change > 0 ? '+' : '−'}{text} vs previous {period}</span>;
}

/** Horizontal bars; each row: { key, label, value, display, href }. */
export function HBars({ rows, max, label }) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value || 0));
  return (
    <div className="hbars" role="img" aria-label={`${label}: ${rows.map((r) => `${r.label} ${r.display ?? r.value}`).join(', ')}`}>
      {rows.map((r) => (
        <div className="bar-row wide" key={r.key}>
          <span title={r.label}>{r.href ? <Link to={appHref(r.href)}>{r.label}</Link> : r.label}</span>
          <div className="bar-track"><div className="bar" style={{ width: `${Math.round(((r.value || 0) / top) * 100)}%` }} /></div>
          <span className="bar-val">{r.display ?? r.value}</span>
        </div>
      ))}
    </div>
  );
}

/** A card with a chart and a "View as table" / "View as chart" toggle. */
export function ChartSection({ title, sub, chart, table, defaultChart = true, empty, children }) {
  const [asChart, setAsChart] = useState(defaultChart);
  return (
    <section className="card pad">
      <div className="section-head">
        <div><h2>{title}</h2>{sub && <div className="sub">{sub}</div>}</div>
        {!empty && (
          <button type="button" className="toggle" aria-pressed={asChart} onClick={() => setAsChart(!asChart)}>
            {asChart ? 'View as table' : 'View as chart'}
          </button>
        )}
      </div>
      {empty ? <p className="small muted">{empty}</p> : (asChart ? chart : <div className="scroll">{table}</div>)}
      {children}
    </section>
  );
}

function CountTable({ items, label = statusLabel }) {
  return (
    <table className="compact" style={{ marginTop: 10 }}>
      <tbody>
        {items.map((item) => (
          <tr key={item.value}>
            <td><Link to={appHref(item.href)}>{label(item.value)}</Link></td>
            <td className="n">{item.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EmployeeTab() {
  const { data, error, loading } = useLoad(() => api.employeeDashboard(), []);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <div className="grid" style={{ '--min': '260px' }}>
      <section className="card pad">
        <h2>My tickets by status</h2>
        <div className="sub">Tickets you reported or follow</div>
        <CountTable items={data.status_counts.filter((s) => s.count > 0)} />
        {data.status_counts.every((s) => !s.count) && <p className="small muted">No tickets yet.</p>}
      </section>
      <section className="card pad good">
        <h2>Awaiting your confirmation</h2>
        <div className="big" style={{ marginTop: 10, color: 'var(--green)' }}>{data.awaiting_confirmation.length}</div>
        {data.awaiting_confirmation.map((item) => (
          <div key={item.id} style={{ marginTop: 6 }}>
            <Link to={`/incidents/${item.id}`} style={{ fontSize: 14 }}>{item.number} · {item.title}</Link>
            {item.seconds_until_auto_close != null && (
              <div className="tiny muted" title={item.auto_close_at ? new Date(item.auto_close_at).toLocaleString() : undefined}>
                {item.seconds_until_auto_close > 60
                  ? `Closes automatically in ${duration(item.seconds_until_auto_close)}`
                  : 'Closes automatically any moment now'}
              </div>
            )}
          </div>
        ))}
      </section>
      <section className="card pad">
        <h2>Recent updates</h2>
        <ul style={{ margin: '10px 0 0', padding: '0 0 0 18px', fontSize: 14, lineHeight: 1.7 }}>
          {data.recent_public_notes.map((note) => (
            <li key={note.id}>
              <Link to={`/incidents/${note.incident_id}`}>{note.body.length > 70 ? `${note.body.slice(0, 70)}…` : note.body}</Link>
              <span className="tiny muted"> · {timeAgo(note.created_at)}</span>
            </li>
          ))}
        </ul>
        {data.recent_public_notes.length === 0 && <p className="small muted">No updates yet.</p>}
      </section>
    </div>
  );
}

function EngineerTab() {
  const { data, error, loading, reload } = useLoad(() => api.engineerDashboard(), []);
  const dir = useDirectory();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const change = async (event) => {
    setSaving(true);
    setSaveError(null);
    try {
      await api.setMyAvailability(event.target.value);
      reload();
      dir.reload();
    } catch (err) {
      setSaveError(err);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="grid" style={{ '--min': '260px' }}>
      <section className="card pad">
        <h2>My availability</h2>
        <div className="sub">Admins see this when assigning work</div>
        <select className="sel" style={{ marginTop: 12 }} value={data.availability} onChange={change} disabled={saving} aria-label="My availability">
          {['Available', 'Busy', 'Off-duty'].map((a) => <option key={a}>{a}</option>)}
        </select>
        {saveError && <div style={{ marginTop: 10 }}><ErrorBox error={saveError} /></div>}
      </section>
      <section className="card pad">
        <h2>My queue</h2>
        <div className="sub">Assigned to you, not final</div>
        <CountTable items={data.status_counts} />
        <Link to="/queue" style={{ fontSize: 14, display: 'inline-block', marginTop: 10 }}>Open my queue</Link>
      </section>
      {data.sla_health && (
        <section className="card pad">
          <h2>SLA health</h2>
          <div className="sub">Your non-final tickets by resolve deadline</div>
          <table className="compact" style={{ marginTop: 10 }}>
            <tbody>
              {data.sla_health.map((item) => (
                <tr key={item.value}>
                  <td><SlaChip state={item.value} /></td>
                  <td className="n"><Link to={appHref(item.href)} aria-label={`${item.count} tickets ${item.value.replace('_', ' ')}`}>{item.count}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      <section className="card pad">
        <h2>Completed this week</h2>
        <div className="sub">Resolved by you since Monday</div>
        <div className="big" style={{ marginTop: 10 }}>{data.resolved_this_week.count}</div>
        <div className="small muted">
          {data.resolved_this_week.mean_seconds != null
            ? `Avg. ${duration(data.resolved_this_week.mean_seconds)} from report to resolve`
            : 'Nothing resolved yet this week'}
        </div>
      </section>
    </div>
  );
}

function CompanyTotals({ totals, period }) {
  const cards = [
    ['created', 'Reported', 'Created in the period', null],
    ['resolved', 'Resolved', 'Resolved in the period', 'up'],
    ['open_now', 'Open now', 'All non-final tickets right now', null],
  ];
  return (
    <div className="grid" style={{ '--min': '200px' }}>
      {cards.map(([key, label, hint, better]) => {
        const item = totals[key];
        if (!item) return null;
        return (
          <Link key={key} className="card stat" to={appHref(item.href)}>
            <div className="stat-label">{label}</div>
            <div className="big">{item.value ?? '–'}</div>
            {key === 'open_now' ? <span className="delta">{hint}</span> : <Delta value={item} better={better} period={period} />}
          </Link>
        );
      })}
    </div>
  );
}

function AdminTab() {
  const dir = useDirectory();
  const [days, setDays] = useState('30');
  const [building, setBuilding] = useState('');
  const [level, setLevel] = useState('floors');
  const { data, error, loading } = useLoad(() => api.adminDashboard(cohortQuery(days, building)), [days, building]);

  const filters = (
    <div className="filters">
      <label>Created
        <select className="sel" value={days} onChange={(e) => setDays(e.target.value)}>
          {PERIODS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>Building
        <select className="sel" value={building} onChange={(e) => setBuilding(e.target.value)}>
          <option value="">All buildings</option>
          {dir.buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
      </label>
      <span className="tiny muted" style={{ marginLeft: 'auto' }}>Every number links to the matching ticket list</span>
    </div>
  );
  if (loading && !data) return <>{filters}<Loading /></>;
  if (error) return <>{filters}<ErrorBox error={error} /></>;

  const periodText = `${days} days`;
  const company = data.company || {};
  const count = (list, value) => list.find((s) => s.value === value) || { count: 0, href: null };
  const unassignedOpen = data.workload.find((w) => !w.assigned_to && w.status === 'Open');
  const workload = new Map();
  for (const row of data.workload) {
    const key = row.assigned_to || 'none';
    workload.set(key, { ...(workload.get(key) || {}), [row.status]: row });
  }
  const engineerRows = [...data.engineers.map((e) => [e.id, e.full_name, e.availability]), ['none', 'Unassigned', '']];
  const recurring = (floorId) => data.recurring
    .filter((r) => r.location_id === floorId || dir.buildings.some((b) => b.floors.some((f) => f.id === floorId && f.seats.some((s) => s.id === r.location_id))))
    .map((r) => `${dir.category(r.category_id).split(' › ')[0]} × ${r.count}`)
    .join(', ');
  const cell = (key, status) => {
    const row = workload.get(key)?.[status];
    return row ? <Link to={appHref(row.href)}>{row.count}</Link> : (key === 'none' && status !== 'Open' ? '–' : 0);
  };

  const rates = company.incident_rate_by_building || [];
  const problems = company.top_problems || [];
  const problemName = (p) => (p.parent_name ? `${p.parent_name} › ${p.name}` : p.name);
  const teamTimes = company.time_by_team || [];
  const onTime = company.on_time_completion || { by_team: [], by_building: [] };
  const sla = data.sla_compliance || [];
  const comms = data.communication_health;
  const routing = data.routing_health;
  const availability = data.availability_counts || [];
  const categories = data.categories || [];
  const hotspots = data.hotspots || {};
  const spots = hotspots[level] || [];
  const openStatuses = data.status_counts || [];
  // Filters matching the dashboard cohort, for links built here.
  const cohort = { building_id: data.cohort?.building_id || undefined };
  const resolvedIn = { ...cohort, resolved_from: data.cohort?.from, resolved_to: data.cohort?.to };
  const createdIn = { ...cohort, created_from: data.cohort?.from, created_to: data.cohort?.to };
  const teamHref = (teamId) => searchHref({ ...resolvedIn, team_id: teamId });
  const buildingHref = (buildingId) => searchHref({ ...resolvedIn, building_id: buildingId });
  const slaHref = (priority) => searchHref({ ...createdIn, priority, status: ['Resolved', 'Closed'] });
  const spotName = (spot) => {
    if (level === 'buildings') return spot.name;
    for (const b of dir.buildings) {
      for (const f of b.floors) {
        if (level === 'floors' && f.id === spot.id) return `${b.code} · ${spot.name}`;
        if (level === 'seats' && f.seats.some((seat) => seat.id === spot.id)) return `${b.code} · ${f.name} · ${spot.name}`;
      }
    }
    return spot.name;
  };

  return (
    <>
      {filters}
      <div>
        <h2 style={{ fontSize: 13, fontWeight: 500, color: 'var(--muted)', marginBottom: 8 }}>Right now · all non-final tickets, any date</h2>
        <div className="grid" style={{ '--min': '200px' }}>
          {[
            ['Open, unassigned', unassignedOpen?.count || 0, unassignedOpen?.href, null],
            ['In progress', count(data.status_counts, 'In Progress').count, count(data.status_counts, 'In Progress').href, null],
            ['Blocked', count(data.status_counts, 'Blocked').count, count(data.status_counts, 'Blocked').href, 'var(--red)'],
            ['Pending escalations', data.pending_escalations.length, '/api/v1/incidents?escalation=Pending', 'var(--warn-ink)'],
          ].map(([label, value, href, color]) => (
            <Link key={label} className="card stat" to={appHref(href)}>
              <div className="stat-label">{label}</div>
              <div className="big" style={{ color: color || undefined }}>{value}</div>
            </Link>
          ))}
        </div>
      </div>

      {company.totals && (
        <div>
          <h2 style={{ fontSize: 13, fontWeight: 500, color: 'var(--muted)', marginBottom: 8 }}>Selected period · last {periodText}</h2>
          <CompanyTotals totals={company.totals} period={periodText} />
        </div>
      )}

      <div className="grid" style={{ '--min': '380px' }}>
        <ChartSection
          title="Open tickets by status"
          sub="All non-final tickets right now; ignores the date range"
          empty={openStatuses.some((s) => s.count) ? null : 'No open tickets.'}
          chart={<HBars label="Open tickets by status" rows={openStatuses.map((s) => ({ key: s.value, label: statusLabel(s.value), value: s.count, href: s.href }))} />}
          table={<CountTable items={openStatuses} />}
        />

        <ChartSection
          title="Open tickets by priority"
          sub="All non-final tickets right now; ignores the date range"
          defaultChart={false}
          chart={<HBars label="Open tickets by priority" rows={data.priority_counts.map((p) => ({ key: p.value, label: p.value, value: p.count, href: p.href }))} />}
          table={<CountTable items={data.priority_counts} label={(v) => v} />}
        />

        <section className="card pad">
          <h2>Engineer workload</h2><div className="sub">All non-final tickets right now, by status; ignores the date range</div>
          <div className="scroll">
            <table className="compact" style={{ marginTop: 10 }}>
              <thead><tr><th>Engineer</th><th>Availability</th><th className="n">Open</th><th className="n">In prog.</th><th className="n">Blocked</th></tr></thead>
              <tbody>
                {engineerRows.map(([key, name, availability]) => (
                  <tr key={key}>
                    <td className={key === 'none' ? 'muted' : ''}>{name}</td>
                    <td>{availability === 'Off-duty' ? 'Off duty' : availability}</td>
                    <td className="n">{cell(key, 'Open')}</td>
                    <td className="n">{cell(key, 'In Progress')}</td>
                    <td className="n">{cell(key, 'Blocked')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <ChartSection
          title="Engineer availability"
          sub="Active engineers right now"
          empty={availability.length ? null : 'No active engineers.'}
          chart={<HBars label="Engineers by availability" rows={availability.map((a) => ({ key: a.value, label: a.value === 'Off-duty' ? 'Off duty' : a.value, value: a.count }))} />}
          table={(
            <table className="compact" style={{ marginTop: 10 }}>
              <tbody>
                {availability.map((a) => (
                  <tr key={a.value}><td>{a.value === 'Off-duty' ? 'Off duty' : a.value}</td><td className="n">{a.count}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        >
          <Link to="/engineers" style={{ fontSize: 14, display: 'inline-block', marginTop: 10 }}>Manage engineers</Link>
        </ChartSection>

        <section className="card pad">
          <h2>Lifecycle speed</h2><div className="sub">Tickets created in the period that reached each step</div>
          <div className="scroll">
            <table className="compact" style={{ marginTop: 10 }}>
              <thead><tr><th>From report to…</th><th className="n">Average</th><th className="n">Median</th><th className="n">Sample</th></tr></thead>
              <tbody>
                {[['assigned', 'First assignment'], ['acknowledged', 'Acknowledged'], ['resolved', 'Resolved']].map(([key, label]) => (
                  <tr key={key}>
                    <td>{label}</td>
                    <td className="n">{duration(data.lifecycle[key].mean_seconds)}</td>
                    <td className="n">{duration(data.lifecycle[key].median_seconds)}</td>
                    <td className="n"><Link to={appHref(data.lifecycle[key].href)}>{data.lifecycle[key].sample_count}</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <ChartSection
          title="Top problems"
          sub="Most reported subcategories in the period"
          empty={problems.length ? null : 'No tickets in this period.'}
          chart={<HBars label="Top problems" rows={problems.map((p) => ({ key: p.category_id, label: problemName(p), value: p.count, display: `${p.count} · ${pct(p.share)}`, href: p.href }))} />}
          table={(
            <table className="compact" style={{ marginTop: 10 }}>
              <thead><tr><th>Problem</th><th className="n">Tickets</th><th className="n">Share</th></tr></thead>
              <tbody>
                {problems.map((p) => (
                  <tr key={p.category_id}>
                    <td><Link to={appHref(p.href)}>{problemName(p)}</Link></td>
                    <td className="n">{p.count}</td>
                    <td className="n">{pct(p.share)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        />

        <ChartSection
          title="Top categories"
          sub="Tickets created in the period, by top-level category"
          empty={categories.length ? null : 'No tickets in this period.'}
          chart={<HBars label="Top categories" rows={categories.map((c) => ({ key: c.id, label: c.name, value: c.count, href: c.href }))} />}
          table={(
            <table className="compact" style={{ marginTop: 10 }}>
              <thead><tr><th>Category</th><th className="n">Tickets</th></tr></thead>
              <tbody>
                {categories.map((c) => (
                  <tr key={c.id}><td><Link to={appHref(c.href)}>{c.name}</Link></td><td className="n">{c.count}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        />

        <ChartSection
          title="Incident rate by building"
          sub="Tickets created in the period per 100 active seats"
          empty={rates.length ? null : 'No active buildings.'}
          chart={<HBars label="Tickets per 100 seats" rows={rates.map((r) => ({ key: r.building_id, label: r.name, value: r.rate_per_100_seats || 0, display: r.rate_per_100_seats == null ? 'n/a' : r.rate_per_100_seats, href: r.href }))} />}
          table={(
            <table className="compact" style={{ marginTop: 10 }}>
              <thead><tr><th>Building</th><th className="n">Tickets</th><th className="n">Active seats</th><th className="n">Per 100 seats</th></tr></thead>
              <tbody>
                {rates.map((r) => (
                  <tr key={r.building_id}>
                    <td><Link to={appHref(r.href)}>{r.name}</Link></td>
                    <td className="n">{r.tickets}</td>
                    <td className="n">{r.active_seats}</td>
                    <td className="n">{r.rate_per_100_seats == null ? 'n/a' : r.rate_per_100_seats}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        />

        <ChartSection
          title="Time to resolve by team"
          sub="Tickets resolved in the period, credited to the resolver's team"
          empty={teamTimes.length ? null : 'No teams yet.'}
          chart={<HBars label="Median time to resolve by team" rows={teamTimes.map((t) => ({ key: t.team_id, label: t.name, value: t.median_seconds || 0, display: `${duration(t.median_seconds)} median`, href: teamHref(t.team_id) }))} />}
          table={(
            <table className="compact" style={{ marginTop: 10 }}>
              <thead><tr><th>Team</th><th className="n">Mean</th><th className="n">Median</th><th className="n">Resolved</th><th className="n">On time</th></tr></thead>
              <tbody>
                {teamTimes.map((t) => (
                  <tr key={t.team_id}>
                    <td><Link to={teamHref(t.team_id)}>{t.name}</Link></td>
                    <td className="n">{duration(t.mean_seconds)}</td>
                    <td className="n">{duration(t.median_seconds)}</td>
                    <td className="n">{t.count}</td>
                    <td className="n">{pct(t.on_time_ratio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        />

        <ChartSection
          title="On-time completion"
          sub="Resolved by the resolve deadline ÷ resolved in the period"
          empty={onTime.by_team.length || onTime.by_building.length ? null : 'Nothing resolved in this period.'}
          chart={(
            <>
              <h3 className="small muted" style={{ fontWeight: 500, marginTop: 14 }}>By team</h3>
              <HBars label="On time by team" max={1} rows={onTime.by_team.map((t) => ({ key: t.team_id, label: t.name, value: t.on_time_ratio || 0, display: `${pct(t.on_time_ratio)} of ${t.resolved}`, href: teamHref(t.team_id) }))} />
              <h3 className="small muted" style={{ fontWeight: 500, marginTop: 18 }}>By building</h3>
              <HBars label="On time by building" max={1} rows={onTime.by_building.map((b) => ({ key: b.building_id, label: b.name, value: b.on_time_ratio || 0, display: `${pct(b.on_time_ratio)} of ${b.resolved}`, href: buildingHref(b.building_id) }))} />
            </>
          )}
          table={(
            <table className="compact" style={{ marginTop: 10 }}>
              <thead><tr><th>Team / building</th><th className="n">Resolved</th><th className="n">On time</th></tr></thead>
              <tbody>
                {onTime.by_team.map((t) => (
                  <tr key={`t-${t.team_id}`}><td><Link to={teamHref(t.team_id)}>{t.name}</Link> <span className="tiny muted">team</span></td><td className="n">{t.resolved}</td><td className="n">{pct(t.on_time_ratio)}</td></tr>
                ))}
                {onTime.by_building.map((b) => (
                  <tr key={`b-${b.building_id}`}><td><Link to={buildingHref(b.building_id)}>{b.name}</Link> <span className="tiny muted">building</span></td><td className="n">{b.resolved}</td><td className="n">{pct(b.on_time_ratio)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        />

        <ChartSection
          title="SLA compliance by priority"
          sub="Tickets created in the period and resolved by their deadline"
          empty={sla.length ? null : 'Nothing resolved yet from this period.'}
          chart={<HBars label="SLA compliance by priority" max={1} rows={sla.map((s) => ({ key: s.priority, label: s.priority, value: s.compliance || 0, display: `${pct(s.compliance)} of ${s.resolved}`, href: slaHref(s.priority) }))} />}
          table={(
            <table className="compact" style={{ marginTop: 10 }}>
              <thead><tr><th>Priority</th><th className="n">Resolved</th><th className="n">Met SLA</th></tr></thead>
              <tbody>
                {sla.map((s) => (
                  <tr key={s.priority}><td><Link to={slaHref(s.priority)}>{s.priority}</Link></td><td className="n">{s.resolved}</td><td className="n">{pct(s.compliance)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        />

        {comms && (
          <section className="card pad">
            <h2>Communication health</h2><div className="sub">How well requesters are kept informed</div>
            <dl className="defs">
              <div>
                <dt>Recent public update<span className="def">Share of non-final tickets with a public note in the last 48 hours (right now).</span></dt>
                <dd className="big">{pct(comms.recent_public_update_share)}</dd>
              </div>
              <div>
                <dt>Public notes per resolved ticket<span className="def">Public notes on tickets resolved in the period ÷ those tickets.</span></dt>
                <dd className="big">{comms.public_notes_per_resolved == null ? '–' : comms.public_notes_per_resolved.toFixed(1)}</dd>
              </div>
              <div>
                <dt>Reopen rate<span className="def">Resolutions rejected by the requester ÷ resolutions, both in the period.</span></dt>
                <dd className="big">{pct(comms.reopen_rate)}</dd>
              </div>
            </dl>
          </section>
        )}

        {routing && (
          <section className="card pad">
            <div className="section-head">
              <div><h2>Routing health</h2><div className="sub">How new tickets find an owner</div></div>
              <Link to="/triage" style={{ fontSize: 14 }}>Open triage</Link>
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
              <span className="big">{pct(routing.auto_assigned_share)}</span>
              <span className="small muted">auto-assigned ({routing.auto_assigned ?? 0} of {routing.created ?? 0} created in the period)</span>
            </div>
            <h3 className="small muted" style={{ fontWeight: 500, marginTop: 14 }}>Triage queue now, by reason</h3>
            {routing.triage_queue.length ? (
              <table className="compact" style={{ marginTop: 4 }}>
                <tbody>
                  {routing.triage_queue.map((q) => (
                    <tr key={q.reason || 'none'}><td><Link to={appHref(q.href)}>{triageReason(q.reason)}</Link></td><td className="n">{q.count}</td></tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="small muted">The triage queue is empty.</p>}
            <h3 className="small muted" style={{ fontWeight: 500, marginTop: 14 }}>Returned to triage in the period, by team</h3>
            {routing.returned_by_team.length ? (
              <table className="compact" style={{ marginTop: 4 }}>
                <tbody>
                  {routing.returned_by_team.map((r) => (
                    <tr key={r.team_id}><td><Link to={appHref(r.href)}>{r.name}</Link></td><td className="n">{r.count}</td></tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="small muted">No tickets were returned.</p>}
          </section>
        )}

        <section className="card pad">
          <div className="section-head">
            <div><h2>Location hotspots</h2><div className="sub">Top 10 by tickets created in the period</div></div>
            <div className="seg" role="group" aria-label="Location level">
              {HOTSPOT_LEVELS.map(([value, label]) => (
                <button key={value} type="button" className={level === value ? 'on' : ''} aria-pressed={level === value} onClick={() => setLevel(value)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="scroll">
            <table className="compact" style={{ marginTop: 10 }}>
              <thead><tr><th>{HOTSPOT_LEVELS.find(([value]) => value === level)[1].slice(0, -1)}</th><th className="n">Tickets</th>{level === 'floors' && <th>Recurring</th>}</tr></thead>
              <tbody>
                {spots.map((spot) => (
                  <tr key={spot.id}>
                    <td><Link to={appHref(spot.href)}>{spotName(spot)}</Link></td>
                    <td className="n">{spot.count}</td>
                    {level === 'floors' && <td>{recurring(spot.id)}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {spots.length === 0 && <p className="small muted">No tickets in this period.</p>}
        </section>

        <section className="card pad">
          <h2>Escalated and blocked</h2><div className="sub">Right now; ignores the date range</div>
          <table className="compact" style={{ marginTop: 10 }}>
            <thead><tr><th>Ticket</th><th>State</th><th className="n">Age</th></tr></thead>
            <tbody>
              {data.pending_escalations.map((e) => (
                <tr key={`e-${e.id}`}><td><Link to={`/incidents/${e.id}`}>{e.number}</Link></td><td>Escalation pending</td><td className="n">{age(e.requested_at)}</td></tr>
              ))}
              {data.blocked.map((b) => (
                <tr key={`b-${b.id}`}><td><Link to={`/incidents/${b.id}`}>{b.number}</Link></td><td>Blocked · {b.blocked_reason?.toLowerCase()}</td><td className="n">{age(b.blocked_since)}</td></tr>
              ))}
            </tbody>
          </table>
          {!data.pending_escalations.length && !data.blocked.length && <p className="small muted">Nothing escalated or blocked.</p>}
        </section>

        <section className="card pad">
          <h2>Communication</h2><div className="sub">Last public note on non-final tickets, right now</div>
          <table className="compact" style={{ marginTop: 10 }}>
            <thead><tr><th>Ticket</th><th>Last public note</th></tr></thead>
            <tbody>
              {data.communication.slice(0, 8).map((c) => (
                <tr key={c.id}>
                  <td><Link to={`/incidents/${c.id}`}>{c.number}</Link></td>
                  <td style={c.last_public_update ? undefined : { color: 'var(--red)', fontWeight: 500 }}>
                    {c.last_public_update ? timeAgo(c.last_public_update) : 'No public note'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </>
  );
}

export default function Dashboard() {
  const { isAdmin, isEngineer, isLead } = useAuth();
  const tabs = [
    ['employee', 'Employee'],
    isEngineer && ['engineer', 'Engineer'],
    isLead && ['team', 'Team'],
    isAdmin && ['admin', 'Admin'],
  ].filter(Boolean);
  const [tab, setTab] = useState(tabs[tabs.length - 1][0]);
  return (
    <>
      <div className="page-head"><div><h1>Dashboard</h1></div></div>
      {tabs.length > 1 && (
        <div className="tabs" role="tablist" aria-label="Dashboard view">
          {tabs.map(([key, label]) => (
            <button key={key} type="button" role="tab" className={tab === key ? 'on' : ''} aria-selected={tab === key} onClick={() => setTab(key)}>
              {label}
            </button>
          ))}
        </div>
      )}
      {tab === 'employee' && <EmployeeTab />}
      {tab === 'engineer' && <EngineerTab />}
      {tab === 'team' && <TeamSummary embedded />}
      {tab === 'admin' && <AdminTab />}
    </>
  );
}
