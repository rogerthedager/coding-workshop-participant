import { useState } from 'react';
import { api } from '../api';
import { useDirectory } from '../directory';
import { useLoad } from '../hooks';
import { Availability, ErrorBox, Loading } from '../components/ui';
import './Admin.css';

const NO_DUTY = 'This team owns categories but nobody is on duty — new tickets go to admin triage.';

function ActiveChip({ on }) {
  return <span className={`chip ${on ? 'adm-on' : 'adm-off'}`}>{on ? 'Active' : 'Inactive'}</span>;
}

/** Turn an API error into a sentence an admin can act on. */
function explain(err, { engineers = [], teams = [] } = {}) {
  if (err?.code === 'team_has_members') {
    const n = err.details?.count;
    return `Move the team's engineers first${n ? ` (${n} still assigned)` : ''}: open Members, clear the list and save, then deactivate.`;
  }
  if (err?.code === 'engineer_in_other_team') {
    const id = err.details?.engineer_user_id;
    const eng = engineers.find((e) => e.user_id === id);
    const team = eng && teams.find((t) => t.id === eng.team_id);
    const who = eng ? eng.user.full_name : 'One of the selected engineers';
    return `${who} already belongs to ${team ? `"${team.name}"` : 'another team'}. Remove them from that team's members first, then add them here.`;
  }
  if (err?.code === 'team_inactive') return 'This team is inactive. Reactivate it before changing its members.';
  if (err?.code === 'duplicate') return `${err.detail}. Pick a different person.`;
  return err?.detail || err?.message || String(err);
}

function TeamEditor({ team, users, onSave, onCancel }) {
  const [name, setName] = useState(team.name);
  const [lead, setLead] = useState(team.lead?.id || '');
  const [active, setActive] = useState(team.is_active);
  const save = (event) => {
    event.preventDefault();
    const body = {};
    if (name.trim() && name.trim() !== team.name) body.name = name.trim();
    if (lead && lead !== team.lead?.id) body.lead_user_id = lead;
    if (active !== team.is_active) body.is_active = active;
    onSave(body);
  };
  return (
    <form className="adm-form" onSubmit={save}>
      <h3>Edit {team.name}</h3>
      <div className="filters">
        <label>Team name
          <input className="input" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>Team lead
          <select className="sel" value={lead} onChange={(e) => setLead(e.target.value)}>
            {!team.lead && <option value="">Choose…</option>}
            {users.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
          </select>
        </label>
        <label className="adm-narrow">Status
          <select className="sel" value={String(active)} onChange={(e) => setActive(e.target.value === 'true')}>
            <option value="true">Active</option>
            <option value="false">Inactive</option>
          </select>
        </label>
      </div>
      <p className="help" style={{ margin: 0 }}>
        Changing the lead moves the lead role to the new person. A team can only be deactivated once it has no members.
      </p>
      <div className="actions">
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn primary">Save team</button>
      </div>
    </form>
  );
}

function MembersEditor({ team, engineers, teams, onSave, onCancel }) {
  const [picked, setPicked] = useState(() => new Set(team.members.map((m) => m.id)));
  const teamName = (id) => teams.find((t) => t.id === id)?.name || 'another team';
  const choices = engineers.filter((e) => e.is_active || picked.has(e.user_id));
  const toggle = (id) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };
  return (
    <form className="adm-form" onSubmit={(e) => { e.preventDefault(); onSave([...picked]); }}>
      <h3>Members of {team.name}</h3>
      <p className="help" style={{ margin: 0 }}>
        Saving replaces the whole member list. Engineers in another team must be removed there first.
      </p>
      {choices.length === 0 ? <div className="muted small">No active engineers yet.</div> : (
        <fieldset style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
          <legend className="sr-only">Engineers in {team.name}</legend>
          <div className="adm-checks">
            {choices.map((e) => {
              const elsewhere = e.team_id && e.team_id !== team.id;
              return (
                <label key={e.user_id} className="adm-check">
                  <input type="checkbox" checked={picked.has(e.user_id)} onChange={() => toggle(e.user_id)} />
                  <span>{e.user.full_name}</span>
                  <Availability value={e.availability} />
                  {elsewhere && <span className="tiny muted">in {teamName(e.team_id)}</span>}
                </label>
              );
            })}
          </div>
        </fieldset>
      )}
      <div className="actions">
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn primary">Save members ({picked.size})</button>
      </div>
    </form>
  );
}

function GroupEditor({ group, teams, onSave, onCancel }) {
  const [form, setForm] = useState({
    name: group.name, team_id: group.team_id || '', sort_order: String(group.sort_order), is_active: group.is_active,
  });
  const save = (event) => {
    event.preventDefault();
    const body = {};
    if (form.name.trim() && form.name.trim() !== group.name) body.name = form.name.trim();
    if ((form.team_id || null) !== group.team_id) body.team_id = form.team_id || null;
    const order = Number(form.sort_order);
    if (form.sort_order !== '' && order !== group.sort_order) body.sort_order = order;
    if (form.is_active !== group.is_active) body.is_active = form.is_active;
    onSave(body);
  };
  return (
    <form className="adm-form" onSubmit={save}>
      <div className="filters">
        <label>Group name
          <input className="input" value={form.name} maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label>Owning team
          <select className="sel" value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value })}>
            <option value="">No team (admin triage)</option>
            {teams.filter((t) => t.is_active || t.id === group.team_id).map((t) => (
              <option key={t.id} value={t.id}>{t.name}{t.is_active ? '' : ' (inactive)'}</option>
            ))}
          </select>
        </label>
        <label className="adm-narrow">Order
          <input className="input" type="number" min={0} max={10000} value={form.sort_order}
            onChange={(e) => setForm({ ...form, sort_order: e.target.value })} />
        </label>
        <label className="adm-narrow">Status
          <select className="sel" value={String(form.is_active)} onChange={(e) => setForm({ ...form, is_active: e.target.value === 'true' })}>
            <option value="true">Active</option>
            <option value="false">Inactive</option>
          </select>
        </label>
      </div>
      <div className="actions">
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn primary">Save group</button>
      </div>
    </form>
  );
}

/** Admin set-up of teams, members and the category groups that route new tickets. */
export default function TeamsAdmin() {
  const dir = useDirectory();
  const { data, error: loadError, loading, reload } = useLoad(
    () => Promise.all([api.teams(), api.categoryGroups(), api.allCategories(), api.users(), api.engineers()])
      .then(([teams, groups, categories, users, engineers]) => ({
        teams: teams.items, groups: groups.items, categories: categories.items,
        users: users.items, engineers: engineers.items,
      })),
    [],
  );
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [open, setOpen] = useState(null); // { kind: 'team' | 'members' | 'group', id }
  const [newTeam, setNewTeam] = useState(null);
  const [newGroup, setNewGroup] = useState(null);
  const [assign, setAssign] = useState({});
  const [busy, setBusy] = useState(false);

  if (loading && !data) return <Loading />;
  if (loadError) return <ErrorBox error={loadError} />;

  const { teams, groups, categories, users, engineers } = data;
  const activeUsers = users.filter((u) => u.is_active !== false);
  const teamName = (id) => teams.find((t) => t.id === id)?.name;
  const groupName = (id) => groups.find((g) => g.id === id)?.name;
  const topLevel = categories.filter((c) => !c.parent_id);
  const childCount = (id) => categories.filter((c) => c.parent_id === id).length;

  const run = async (action, message) => {
    setError('');
    setNotice('');
    setBusy(true);
    try {
      await action();
      setNotice(message);
      setOpen(null);
      reload();
      dir?.reload();
      return true;
    } catch (err) {
      setError(explain(err, { engineers, teams }));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const createTeam = async (event) => {
    event.preventDefault();
    if (!newTeam.name.trim() || !newTeam.lead) {
      setError('Give the team a name and choose a lead.');
      return;
    }
    const ok = await run(
      () => api.createTeam({ name: newTeam.name.trim(), lead_user_id: newTeam.lead }),
      `Team "${newTeam.name.trim()}" created.`,
    );
    if (ok) setNewTeam(null);
  };

  const createGroup = async (event) => {
    event.preventDefault();
    if (!newGroup.name.trim()) {
      setError('Give the group a name.');
      return;
    }
    const body = { name: newGroup.name.trim(), team_id: newGroup.team_id || null };
    if (newGroup.sort_order !== '') body.sort_order = Number(newGroup.sort_order);
    const ok = await run(() => api.createCategoryGroup(body), `Group "${body.name}" created.`);
    if (ok) setNewGroup(null);
  };

  const changed = Object.entries(assign).filter(([id, value]) => {
    const cat = categories.find((c) => c.id === id);
    return cat && (cat.group_id || '') !== value;
  });
  const saveAssignments = async () => {
    const ok = await run(
      () => Promise.all(changed.map(([id, value]) => api.updateCategory(id, { group_id: value || null }))),
      `${changed.length} categor${changed.length === 1 ? 'y' : 'ies'} updated.`,
    );
    if (ok) setAssign({});
  };

  const isOpen = (kind, id) => open?.kind === kind && open.id === id;
  const toggleOpen = (kind, id) => {
    setError('');
    setOpen(isOpen(kind, id) ? null : { kind, id });
  };

  return (
    <div className="adm-stack">
      <div className="page-head">
        <div>
          <h1>Teams and routing</h1>
          <p>Who handles which kinds of tickets.</p>
        </div>
      </div>

      <section className="card pad">
        <p className="adm-intro">
          New tickets are routed by their category&apos;s <strong>group</strong>: the group&apos;s owning team gets
          the ticket and it goes to that team&apos;s best on-duty engineer. If the group has no team, the team has
          nobody on duty, or the category has no group, the ticket goes to <strong>admin triage</strong> instead.
        </p>
      </section>

      {notice && <div className="notice" role="status">{notice}</div>}
      {error && <div className="alert" role="alert">{error}</div>}

      {/* Teams */}
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Teams</h2>
            <div className="sub">{teams.length} team{teams.length === 1 ? '' : 's'}</div>
          </div>
          <button type="button" className="btn primary sm"
            onClick={() => setNewTeam(newTeam ? null : { name: '', lead: '' })}>
            {newTeam ? 'Close' : 'New team'}
          </button>
        </div>
        {newTeam && (
          <form className="adm-form" onSubmit={createTeam}>
            <h3>New team</h3>
            <div className="filters">
              <label>Team name
                <input className="input" value={newTeam.name} maxLength={120}
                  onChange={(e) => setNewTeam({ ...newTeam, name: e.target.value })} />
              </label>
              <label>Team lead
                <select className="sel" value={newTeam.lead} onChange={(e) => setNewTeam({ ...newTeam, lead: e.target.value })}>
                  <option value="">Choose a person…</option>
                  {activeUsers.map((u) => <option key={u.id} value={u.id}>{u.full_name} · {u.email}</option>)}
                </select>
              </label>
            </div>
            <p className="help" style={{ margin: 0 }}>The chosen person gets the team lead role. Each person can lead one team.</p>
            <div className="actions">
              <button type="button" className="btn" onClick={() => setNewTeam(null)}>Cancel</button>
              <button type="submit" className="btn primary" disabled={busy}>Create team</button>
            </div>
          </form>
        )}
        {teams.length === 0 ? <div className="empty">No teams yet. Every new ticket goes to admin triage.</div> : (
          <ul className="adm-list">
            {teams.map((team) => (
              <li key={team.id} className="adm-item">
                <div className="adm-row">
                  <span className="adm-name">{team.name}</span>
                  <ActiveChip on={team.is_active} />
                  <span className="adm-push">
                    <button type="button" className="btn sm" aria-expanded={isOpen('team', team.id)}
                      onClick={() => toggleOpen('team', team.id)}>Edit</button>
                    <button type="button" className="btn sm" aria-expanded={isOpen('members', team.id)}
                      onClick={() => toggleOpen('members', team.id)}>Members</button>
                  </span>
                </div>
                <div className="adm-meta">Lead: {team.lead?.full_name || 'None'}</div>
                {team.warnings?.includes('no_member_on_duty') && (
                  <div className="adm-warn" role="note">{NO_DUTY}</div>
                )}
                <div className="adm-row">
                  <span className="small muted">Members:</span>
                  {team.members.length ? team.members.map((m) => (
                    <span key={m.id} className="adm-row" style={{ gap: 4 }}>
                      <span className="small">{m.full_name}</span>
                      <Availability value={m.availability} />
                    </span>
                  )) : <span className="small muted">None</span>}
                </div>
                <div className="adm-row">
                  <span className="small muted">Category groups:</span>
                  {team.category_groups.length ? team.category_groups.map((g) => (
                    <span key={g.id} className="skill">{g.name}{g.is_active ? '' : ' (inactive)'}</span>
                  )) : <span className="small muted">None</span>}
                </div>
                {isOpen('team', team.id) && (
                  <TeamEditor team={team} users={activeUsers} onCancel={() => setOpen(null)}
                    onSave={(body) => (Object.keys(body).length
                      ? run(() => api.updateTeam(team.id, body), `Team "${body.name || team.name}" saved.`)
                      : setOpen(null))} />
                )}
                {isOpen('members', team.id) && (
                  <MembersEditor team={team} engineers={engineers} teams={teams} onCancel={() => setOpen(null)}
                    onSave={(ids) => run(() => api.setTeamMembers(team.id, ids), `Members of "${team.name}" saved.`)} />
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Category groups */}
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Category groups</h2>
            <div className="sub">A group bundles categories and names the team that handles them.</div>
          </div>
          <button type="button" className="btn primary sm"
            onClick={() => setNewGroup(newGroup ? null : { name: '', team_id: '', sort_order: '' })}>
            {newGroup ? 'Close' : 'New group'}
          </button>
        </div>
        {newGroup && (
          <form className="adm-form" onSubmit={createGroup}>
            <h3>New category group</h3>
            <div className="filters">
              <label>Group name
                <input className="input" value={newGroup.name} maxLength={120}
                  onChange={(e) => setNewGroup({ ...newGroup, name: e.target.value })} />
              </label>
              <label>Owning team
                <select className="sel" value={newGroup.team_id} onChange={(e) => setNewGroup({ ...newGroup, team_id: e.target.value })}>
                  <option value="">No team (admin triage)</option>
                  {teams.filter((t) => t.is_active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </label>
              <label className="adm-narrow">Order
                <input className="input" type="number" min={0} max={10000} value={newGroup.sort_order} placeholder="0"
                  onChange={(e) => setNewGroup({ ...newGroup, sort_order: e.target.value })} />
              </label>
            </div>
            <div className="actions">
              <button type="button" className="btn" onClick={() => setNewGroup(null)}>Cancel</button>
              <button type="submit" className="btn primary" disabled={busy}>Create group</button>
            </div>
          </form>
        )}
        {groups.length === 0 ? <div className="empty">No category groups yet.</div> : (
          <div className="scroll">
            <table className="compact">
              <thead><tr><th>Group</th><th>Owning team</th><th className="n">Order</th><th>Status</th><th /></tr></thead>
              <tbody>
                {groups.map((g) => isOpen('group', g.id) ? (
                  <tr key={g.id}>
                    <td colSpan={5} style={{ padding: 0 }}>
                      <GroupEditor group={g} teams={teams} onCancel={() => setOpen(null)}
                        onSave={(body) => (Object.keys(body).length
                          ? run(() => api.updateCategoryGroup(g.id, body), `Group "${body.name || g.name}" saved.`)
                          : setOpen(null))} />
                    </td>
                  </tr>
                ) : (
                  <tr key={g.id}>
                    <td style={{ fontWeight: 500 }}>{g.name}</td>
                    <td>{g.team_id ? teamName(g.team_id) || '—' : <span className="muted">None — goes to admin triage</span>}</td>
                    <td className="n">{g.sort_order}</td>
                    <td><ActiveChip on={g.is_active} /></td>
                    <td className="n">
                      <button type="button" className="btn sm" onClick={() => toggleOpen('group', g.id)}>Edit</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Category → group */}
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Which group handles each category</h2>
            <div className="sub">Subcategories inherit their parent&apos;s group, so only top-level categories are listed.</div>
          </div>
          <button type="button" className="btn primary sm" disabled={!changed.length || busy} onClick={saveAssignments}>
            Save {changed.length || ''} change{changed.length === 1 ? '' : 's'}
          </button>
        </div>
        {topLevel.length === 0 ? <div className="empty">No categories yet. Add them under Facilities.</div> : (
          <div className="scroll">
            <table className="compact">
              <thead><tr><th>Category</th><th>Group</th></tr></thead>
              <tbody>
                {topLevel.map((c) => {
                  const value = assign[c.id] ?? (c.group_id || '');
                  const dirty = value !== (c.group_id || '');
                  return (
                    <tr key={c.id}>
                      <td>
                        <div style={{ fontWeight: 500 }}>
                          {c.name}{c.is_active ? '' : <span className="muted"> (inactive)</span>}
                        </div>
                        <div className="tiny muted">
                          {childCount(c.id)} subcategor{childCount(c.id) === 1 ? 'y' : 'ies'}
                          {dirty ? ' · unsaved' : ''}
                        </div>
                      </td>
                      <td>
                        <label className="sr-only" htmlFor={`grp-${c.id}`}>Group for {c.name}</label>
                        <select id={`grp-${c.id}`} className="sel" style={{ maxWidth: '100%' }} value={value}
                          onChange={(e) => setAssign({ ...assign, [c.id]: e.target.value })}>
                          <option value="">No group (admin triage)</option>
                          {groups.map((g) => (
                            <option key={g.id} value={g.id}>{g.name}{g.is_active ? '' : ' (inactive)'}</option>
                          ))}
                        </select>
                        {c.group_id && !groupName(c.group_id) && <div className="tiny muted">Unknown group</div>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
