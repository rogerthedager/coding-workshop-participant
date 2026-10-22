import { age, duration } from '../format';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useDirectory } from '../directory';
import { useLoad } from '../hooks';
import { ErrorBox, Loading } from '../components/ui';
import { cohortQuery } from '../lib';

const PERIODS = [['7', 'Last 7 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days']];
const DAY = 86400000;

/** Count of tickets reported in each week of the period (one list call per week). */
async function weeklyReported(days, buildingId) {
  const end = Date.now();
  const weeks = Math.max(1, Math.round(Number(days) / 7));
  const ranges = Array.from({ length: weeks }, (_, i) => {
    const to = new Date(end - (weeks - 1 - i) * 7 * DAY);
    return { from: new Date(to.getTime() - 7 * DAY), to };
  });
  const totals = await Promise.all(ranges.map(({ from, to }) => api.incidents({
    date_from: from.toISOString(), date_to: to.toISOString(), building_id: buildingId || undefined, page_size: 1,
  }).then((r) => r.total)));
  return ranges.map((range, i) => ({
    label: range.from.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    count: totals[i],
  }));
}

/** The "leader" view: counts only, no ticket contents. */
export default function Overview() {
  const dir = useDirectory();
  const [days, setDays] = useState('30');
  const [building, setBuilding] = useState('');
  const load = useLoad(async () => {
    const query = cohortQuery(days, building);
    const [dashboard, reported, critical, weeks] = await Promise.all([
      api.adminDashboard(query),
      api.incidents({ date_from: query.from, date_to: query.to, building_id: query.building_id, page_size: 1 }),
      api.incidents({ priority: 'Critical', status: ['Open', 'In Progress', 'Blocked'], building_id: query.building_id, page_size: 10 }),
      weeklyReported(days, building),
    ]);
    const longBlocked = dashboard.blocked.filter((b) => Date.now() - new Date(b.blocked_since).getTime() > 2 * DAY);
    return { dashboard, reported: reported.total, critical: critical.items, weeks, longBlocked };
  }, [days, building]);

  const data = load.data;
  const head = (
    <div className="page-head">
      <div>
        <h1>Facility overview</h1>
        <p>How quickly incidents get fixed, where they happen and what needs a decision.</p>
      </div>
      <div className="actions" style={{ alignItems: 'center' }}>
        <label className="sr-only" htmlFor="ov-bld">Building</label>
        <select id="ov-bld" className="sel" value={building} onChange={(e) => setBuilding(e.target.value)}>
          <option value="">All buildings</option>
          {dir.buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <div className="pill-group" role="group" aria-label="Period">
          {PERIODS.map(([value, label]) => (
            <button key={value} type="button" className={days === value ? 'on' : ''} aria-pressed={days === value} onClick={() => setDays(value)}>{label}</button>
          ))}
        </div>
      </div>
    </div>
  );
  if (load.error) return <>{head}<ErrorBox error={load.error} /></>;
  if (!data) return <>{head}<Loading /></>;

  const d = data.dashboard;
  const resolved = d.lifecycle.resolved;
  const maxWeek = Math.max(1, ...data.weeks.map((w) => w.count));
  const attention = [
    ...data.critical.map((t) => ({ kind: 'Critical', cls: 'c-crit', id: t.id, text: `${t.number} · ${t.title}`, age: `Open ${age(t.created_at)}` })),
    ...d.pending_escalations.map((e) => ({ kind: 'Escalation', cls: 'c-esc', id: e.id, text: e.number, age: `Waiting ${age(e.requested_at)}` })),
    ...data.longBlocked.map((b) => ({ kind: 'Blocked', cls: 'c-blk', id: b.id, text: `${b.number} · ${b.blocked_reason}`, age: `Blocked ${age(b.blocked_since)}` })),
  ];
  const kpis = [
    ['Incidents reported', data.reported, 'Created in the selected period'],
    ['Resolved', resolved.sample_count, data.reported ? `${Math.round((resolved.sample_count / data.reported) * 100)}% of reported` : ''],
    ['Avg time to resolve', duration(resolved.mean_seconds), 'From report to resolved'],
    ['Pending escalations', d.pending_escalations.length, 'Waiting on an admin decision'],
  ];

  return (
    <>
      {head}
      <div className="grid" style={{ '--min': '200px' }}>
        {kpis.map(([label, value, hint]) => (
          <div className="card stat" key={label}>
            <div className="stat-label">{label}</div>
            <div className="big">{value}</div>
            <div className="tiny muted">{hint}</div>
          </div>
        ))}
      </div>

      <div className="grid" style={{ '--min': '380px' }}>
        <section className="card pad" aria-labelledby="trend-h" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <h2 id="trend-h">Reported, by week</h2>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, height: 170, borderBottom: '1px solid var(--line)' }}
            role="img" aria-label={`Reported per week: ${data.weeks.map((w) => `${w.label} ${w.count}`).join(', ')}`}>
            {data.weeks.map((w) => (
              <div key={w.label} title={`${w.count} reported`} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end', height: '100%', gap: 4 }}>
                <span className="tiny muted">{w.count}</span>
                <div style={{ width: '60%', maxWidth: 28, borderRadius: '3px 3px 0 0', background: 'var(--blue)', height: `${Math.round((w.count / maxWeek) * 140)}px` }} />
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: -8 }} className="tiny muted">
            {data.weeks.map((w) => <span key={w.label} style={{ flex: 1, textAlign: 'center' }}>{w.label}</span>)}
          </div>
        </section>

        <section className="card pad" aria-labelledby="attn-h">
          <h2 id="attn-h">Needs a decision</h2>
          <p className="small muted" style={{ margin: '4px 0 8px' }}>Critical tickets, escalations waiting on an admin, and tickets blocked longer than 2 days.</p>
          {attention.map((a) => (
            <div key={`${a.kind}-${a.id}`} className="actions" style={{ alignItems: 'center', padding: '10px 0', borderTop: '1px solid var(--line-2)', gap: 12 }}>
              <span className={`chip ${a.cls}`}>{a.kind}</span>
              <Link to={`/incidents/${a.id}`} style={{ flex: '1 1 200px', fontSize: 14 }}>{a.text}</Link>
              <span className="small muted nowrap">{a.age}</span>
            </div>
          ))}
          {attention.length === 0 && <p className="small muted">Nothing needs a decision right now.</p>}
        </section>
      </div>

      <section className="card" aria-labelledby="loc-h">
        <div className="card-head" style={{ paddingBottom: 4 }}>
          <h2 id="loc-h">By location</h2>
          <span className="small muted">Locations with 3 or more tickets in the same category in 30 days are flagged as recurring.</span>
        </div>
        <div className="scroll">
          <table>
            <thead><tr><th>Floor</th><th className="n">Reported</th><th>Top category</th></tr></thead>
            <tbody>
              {d.hotspots.floors.map((floor) => {
                const owner = dir.buildings.find((b) => b.floors.some((f) => f.id === floor.id));
                const seatIds = new Set(owner?.floors.find((f) => f.id === floor.id)?.seats?.map((s) => s.id) || []);
                const recurring = d.recurring.some((r) => r.location_id === floor.id || seatIds.has(r.location_id));
                return (
                  <tr key={floor.id}>
                    <td style={{ fontWeight: 500 }} className="nowrap">
                      {owner ? `${owner.name} · ` : ''}{floor.name}
                      {recurring && <span className="chip c-esc" style={{ marginLeft: 6 }}>Recurring</span>}
                    </td>
                    <td className="n">{floor.count}</td>
                    <td className="muted nowrap">
                      {floor.top_category_id ? `${dir.category(floor.top_category_id)} (${floor.top_category_count})` : ''}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {d.hotspots.floors.length === 0 && <div className="empty">No tickets in this period.</div>}
      </section>
    </>
  );
}
