import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, appHref } from '../api';
import { useAuth } from '../auth';
import { duration, pct } from '../format';
import { useLoad } from '../hooks';
import { Availability, ErrorBox, Loading } from '../components/ui';
import { ChartSection, Delta, HBars } from './Dashboard';
import './Dashboards.css';

const PERIODS = [['week', 'Week'], ['month', 'Month'], ['quarter', 'Quarter']];

/** [key, label, kind, better]: better is 'up', 'down', or null (neutral). */
const TOTALS = [
  ['resolved', 'Resolved', 'count', 'up'],
  ['assigned', 'Assigned', 'count', null],
  ['closed', 'Closed', 'count', 'up'],
  ['on_time_ratio', 'Resolved on time', 'ratio', 'up'],
  ['avg_ack_seconds', 'Avg. time to acknowledge', 'seconds', 'down'],
  ['avg_resolve_seconds', 'Avg. time to resolve', 'seconds', 'down'],
  ['reopened', 'Reopened', 'count', 'down'],
  ['returned_to_triage', 'Returned to triage', 'count', 'down'],
];

const AVAILABILITY_RANK = { Available: 0, Busy: 1, 'Off-duty': 2 };

const COLUMNS = [
  ['full_name', 'Engineer', (r) => r.full_name?.toLowerCase()],
  ['resolved', 'Resolved', (r) => r.resolved],
  ['open_now', 'Open now', (r) => r.open_now],
  ['avg_resolve_seconds', 'Avg. resolve', (r) => r.avg_resolve_seconds],
  ['on_time_ratio', 'On time', (r) => r.on_time_ratio],
  ['reopens', 'Reopens', (r) => r.reopens],
  ['availability', 'Availability', (r) => AVAILABILITY_RANK[r.availability]],
];

function format(kind, value) {
  if (kind === 'seconds') return duration(value);
  if (kind === 'ratio') return pct(value);
  return value ?? '–';
}

function bucketDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function EngineerTable({ rows }) {
  const [sort, setSort] = useState({ key: 'resolved', dir: 'desc' });
  const [, , value] = COLUMNS.find(([key]) => key === sort.key);
  const sorted = [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1; // missing values always last
    if (vb == null) return -1;
    const order = va < vb ? -1 : va > vb ? 1 : 0;
    return sort.dir === 'asc' ? order : -order;
  });
  const toggle = (key) => setSort((s) => (s.key === key
    ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: key === 'full_name' || key === 'availability' ? 'asc' : 'desc' }));

  return (
    <div className="scroll">
      <table className="compact" style={{ marginTop: 10 }}>
        <thead>
          <tr>
            {COLUMNS.map(([key, label]) => {
              const active = sort.key === key;
              return (
                <th key={key} className={key === 'full_name' || key === 'availability' ? '' : 'n'}
                  aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" className="sort-btn" data-active={active} onClick={() => toggle(key)}>
                    {label}<span aria-hidden="true">{active ? (sort.dir === 'asc' ? '▲' : '▼') : ''}</span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.user_id}>
              <td><Link to={appHref(r.href)}>{r.full_name}</Link></td>
              <td className="n">{r.resolved}</td>
              <td className="n">{r.open_now}</td>
              <td className="n">{duration(r.avg_resolve_seconds)}</td>
              <td className="n">{pct(r.on_time_ratio)}</td>
              <td className="n">{r.reopens}</td>
              <td><Availability value={r.availability} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <p className="small muted">No engineers on this team yet.</p>}
    </div>
  );
}

function OverTime({ points, period }) {
  const weekly = period !== 'week';
  const max = Math.max(1, ...points.map((p) => p.count));
  const label = (p) => {
    const date = bucketDate(p.period_start);
    return weekly
      ? date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
      : date.toLocaleDateString(undefined, { weekday: 'short' });
  };
  // Thin out labels when there are many bars so they stay legible on a phone.
  const every = points.length > 8 ? Math.ceil(points.length / 7) : 1;
  return (
    <>
      <div className="vbars" role="img"
        aria-label={`Resolved per ${weekly ? 'week' : 'day'}: ${points.map((p) => `${label(p)} ${p.count}`).join(', ')}`}>
        {points.map((p) => (
          <div key={p.period_start} title={`${label(p)}: ${p.count} resolved`}>
            <span className="tiny muted">{p.count}</span>
            <div className="vbar" style={{ height: `${Math.round((p.count / max) * 140)}px` }} />
          </div>
        ))}
      </div>
      <div className="vlabels tiny muted" aria-hidden="true">
        {points.map((p, i) => <span key={p.period_start}>{i % every === 0 ? label(p) : ''}</span>)}
      </div>
    </>
  );
}

function friendly(error, isAdmin) {
  if (error?.status === 403) {
    return isAdmin
      ? 'Pick a team to see its summary.'
      : "You aren't leading a team yet. Ask an administrator to make you a team's lead to see its summary here.";
  }
  if (error?.status === 404) return 'That team no longer exists. Pick another one.';
  return null;
}

/** `embedded` renders inside the dashboard's Team tab, under its h1. */
export default function TeamSummary({ embedded = false }) {
  const { isAdmin } = useAuth();
  const Heading = embedded ? 'h2' : 'h1';
  const [period, setPeriod] = useState('week');
  const [picked, setPicked] = useState('');
  const teams = useLoad(() => (isAdmin ? api.teams() : Promise.resolve(null)), [isAdmin]);
  const teamList = teams.data?.items || [];
  const teamId = isAdmin ? (picked || teamList[0]?.id || '') : '';
  const waitingForTeams = isAdmin && !teams.data && !teams.error;
  const load = useLoad(
    () => (waitingForTeams || (isAdmin && !teamId)
      ? Promise.resolve(null)
      : api.teamDashboard({ period, team_id: teamId || undefined })),
    [period, teamId, waitingForTeams],
  );
  const data = load.data;
  // Switching team or period keeps the old numbers on screen until the new ones arrive.
  const stale = load.loading && Boolean(data);
  const periodText = period;

  const head = (
    <div className="page-head">
      <div>
        <Heading>{data?.team?.name ? `${data.team.name} summary` : 'Team summary'}</Heading>
        <p>
          {data?.team
            ? `${data.team.members_on_duty} on duty now · compared with the previous ${period}`
            : 'How your team is doing compared with the previous period.'}
        </p>
      </div>
      <div className="actions" style={{ alignItems: 'center' }}>
        <span className="small muted" role="status">{stale ? 'Updating…' : ''}</span>
        {isAdmin && teamList.length > 0 && (
          <>
            <label className="sr-only" htmlFor="ts-team">Team</label>
            <select id="ts-team" className="sel" value={teamId} onChange={(e) => setPicked(e.target.value)}>
              {teamList.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </>
        )}
        <div className="pill-group" role="group" aria-label="Period">
          {PERIODS.map(([value, label]) => (
            <button key={value} type="button" className={period === value ? 'on' : ''} aria-pressed={period === value} onClick={() => setPeriod(value)}>
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );

  if (teams.error) return <>{head}<ErrorBox error={teams.error} /></>;
  if (isAdmin && teams.data && teamList.length === 0) {
    return <>{head}<div className="card empty">No teams yet. <Link to="/admin/teams">Create a team</Link> to see its summary.</div></>;
  }
  if (load.error) {
    const message = friendly(load.error, isAdmin);
    return <>{head}{message ? <div className="card empty">{message}</div> : <ErrorBox error={load.error} />}</>;
  }
  if (!data) return <>{head}<Loading /></>;

  const { totals, engineers, charts } = data;
  const byCategory = charts.resolved_by_category || [];
  const overTime = charts.resolved_over_time || [];
  const unit = period === 'week' ? 'Day' : 'Week of';

  return (
    <>
      {head}

      <div className="ts-body" aria-busy={stale}>
        <div className="grid" style={{ '--min': '200px' }}>
          {[['open_now', 'Open now', 'Assigned to the team, not final'], ['blocked_now', 'Blocked now', 'Waiting on something outside the team']].map(([key, label, hint]) => (
            totals[key] && (
              <Link key={key} className="card stat" to={appHref(totals[key].href)}>
                <div className="stat-label">{label}</div>
                <div className="big" style={key === 'blocked_now' && totals[key].value ? { color: 'var(--red)' } : undefined}>{totals[key].value ?? '–'}</div>
                <span className="delta">{hint}</span>
              </Link>
            )
          ))}
          {TOTALS.map(([key, label, kind, better]) => (
            totals[key] && (
              <div key={key} className="card stat">
                <div className="stat-label">{label}</div>
                <div className="big">{format(kind, totals[key].value)}</div>
                <Delta value={totals[key]} kind={kind} better={better} period={periodText} />
              </div>
            )
          ))}
        </div>

        <section className="card pad">
          <h2>Engineers</h2>
          <div className="sub">Resolved this {period}, credited to whoever resolved it. Click a column to sort.</div>
          <EngineerTable rows={engineers} />
        </section>

        <div className="grid" style={{ '--min': '340px' }}>
          <ChartSection
            title="Resolved over time"
            sub={period === 'week' ? 'Per day' : 'Per week'}
            empty={overTime.length ? null : 'Nothing to show yet.'}
            chart={<OverTime points={overTime} period={period} />}
            table={(
              <table className="compact" style={{ marginTop: 10 }}>
                <thead><tr><th>{unit}</th><th className="n">Resolved</th></tr></thead>
                <tbody>
                  {overTime.map((p) => (
                    <tr key={p.period_start}>
                      <td>{bucketDate(p.period_start).toLocaleDateString(undefined, { weekday: period === 'week' ? 'short' : undefined, month: 'short', day: 'numeric' })}</td>
                      <td className="n">{p.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          />

          <ChartSection
            title="Resolved by category"
            sub={`Top-level category, this ${period}`}
            empty={byCategory.length ? null : `Nothing resolved this ${period}.`}
            chart={<HBars label="Resolved by category" rows={byCategory.map((c) => ({ key: c.category_id, label: c.name || 'Unknown', value: c.count }))} />}
            table={(
              <table className="compact" style={{ marginTop: 10 }}>
                <thead><tr><th>Category</th><th className="n">Resolved</th></tr></thead>
                <tbody>
                  {byCategory.map((c) => (
                    <tr key={c.category_id}><td>{c.name || 'Unknown'}</td><td className="n">{c.count}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          />
        </div>
      </div>
    </>
  );
}
