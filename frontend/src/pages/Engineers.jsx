import { useState } from 'react';
import { api } from '../api';
import { useDirectory } from '../directory';
import { useLoad } from '../hooks';
import { Availability } from '../components/ui';

const SKILLS = [
  'IT and AV equipment', 'Network and Wi-Fi', 'Furniture', 'HVAC and temperature',
  'Electrical and lighting', 'Plumbing and restrooms', 'Cleaning', 'Access and security',
];
// Every engineer has exactly two different skills (FR-4.5).
const REQUIRED_SKILLS = 2;
const AVAILABILITY = ['Available', 'Busy', 'Off-duty'];

function SkillPicker({ value, onChange, id }) {
  const toggle = (skill) => {
    if (value.includes(skill)) onChange(value.filter((s) => s !== skill));
    else if (value.length < REQUIRED_SKILLS) onChange([...value, skill]);
  };
  return (
    <div className="chips" role="group" aria-labelledby={id}>
      {SKILLS.map((skill) => {
        const on = value.includes(skill);
        return (
          <button key={skill} type="button" className={`fchip${on ? ' on' : ''}`} aria-pressed={on}
            disabled={!on && value.length >= REQUIRED_SKILLS} onClick={() => toggle(skill)}>
            {skill}
          </button>
        );
      })}
    </div>
  );
}

/** Home buildings: a tie-breaker when ranking engineers for a ticket. */
function BuildingPicker({ value, onChange, buildings, id }) {
  const toggle = (buildingId) =>
    onChange(value.includes(buildingId) ? value.filter((b) => b !== buildingId) : [...value, buildingId]);
  return (
    <div className="chips" role="group" aria-labelledby={id}>
      {buildings.map((b) => {
        const on = value.includes(b.id);
        return (
          <button key={b.id} type="button" className={`fchip${on ? ' on' : ''}`} aria-pressed={on} onClick={() => toggle(b.id)}>
            {b.code || b.name}
          </button>
        );
      })}
    </div>
  );
}

const skillsHelp = (skills) => (skills.length === REQUIRED_SKILLS ? '' : `Pick ${REQUIRED_SKILLS - skills.length} more.`);
const BLANK = { full_name: '', email: '', phone: '', skills: [], team_id: '', home_building_ids: [] };

/** Admin maintenance of engineer profiles: availability, two skills, team, home buildings, activation. */
export default function Engineers() {
  const dir = useDirectory();
  const teams = useLoad(() => api.teams(), []);
  const teamList = teams.data?.items || [];
  const teamName = (id) => teamList.find((t) => t.id === id)?.name;
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState({});
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(BLANK);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const save = async (engineer) => {
    setError(null);
    setBusy(true);
    try {
      await api.updateEngineer(engineer.id, draft);
      setEditing(null);
      setNotice(`${engineer.user.full_name} updated.`);
      dir.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const create = async (event) => {
    event.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      const created = await api.createEngineer({
        full_name: form.full_name.trim(),
        email: form.email.trim(),
        phone: form.phone.trim() || undefined,
        skills: form.skills,
        team_id: form.team_id || undefined,
        home_building_ids: form.home_building_ids,
      });
      setNotice(created.temporary_password
        ? `${created.user.full_name} added. Temporary password (shown once): ${created.temporary_password}`
        : `${created.user.full_name} added as an engineer.`);
      setAdding(false);
      setForm(BLANK);
      dir.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Engineers</h1>
          <p>Availability, two skills per engineer, team, and who is active. Team members are managed on the Teams page.</p>
        </div>
        <button type="button" className="btn primary" onClick={() => setAdding(!adding)}>Add engineer</button>
      </div>
      {notice && <div className="notice" role="status">{notice}</div>}
      {error && <div className="alert" role="alert">{error.detail}</div>}

      {adding && (
        <form className="card pad" onSubmit={create} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <h2>Add engineer</h2>
          <p className="small muted" style={{ margin: 0 }}>An existing account gets the engineer role. A new account gets a temporary password.</p>
          <div className="grid" style={{ '--min': '220px' }}>
            <label className="field">Full name<input className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
            <label className="field">Work email<input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@acme.inc" /></label>
            <label className="field">Phone (optional)<input className="input" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></label>
          </div>
          <div className="field">
            <span id="new-skills">Skills (exactly {REQUIRED_SKILLS})</span>
            <SkillPicker id="new-skills" value={form.skills} onChange={(skills) => setForm({ ...form, skills })} />
            {skillsHelp(form.skills) && <span className="help">{skillsHelp(form.skills)}</span>}
          </div>
          <div className="grid" style={{ '--min': '220px' }}>
            <label className="field">Team (optional)
              <select className="input" value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value })}>
                <option value="">No team</option>
                {teamList.filter((t) => t.is_active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </label>
            <div className="field">
              <span id="new-home">Home buildings (optional)</span>
              <BuildingPicker id="new-home" buildings={dir.buildings} value={form.home_building_ids}
                onChange={(home_building_ids) => setForm({ ...form, home_building_ids })} />
            </div>
          </div>
          <div className="actions" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={() => setAdding(false)}>Cancel</button>
            <button type="submit" className="btn primary" disabled={busy || form.skills.length !== REQUIRED_SKILLS}>
              {busy ? 'Adding…' : 'Add engineer'}
            </button>
          </div>
        </form>
      )}

      <section className="card">
        <div className="scroll">
          <table>
            <thead><tr><th>Engineer</th><th>Availability</th><th>Skills</th><th>Team</th><th className="n">Active tickets</th><th>Status</th><th /></tr></thead>
            <tbody>
              {dir.engineers.map((e) => editing === e.id ? (
                <tr key={e.id}>
                  <td colSpan={7}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                      <strong>{e.user.full_name}</strong>
                      <div className="filters">
                        <label>Availability
                          <select className="sel" value={draft.availability ?? e.availability} onChange={(ev) => setDraft({ ...draft, availability: ev.target.value })}>
                            {AVAILABILITY.map((a) => <option key={a}>{a}</option>)}
                          </select>
                        </label>
                        <label>Profile
                          <select className="sel" value={String(draft.is_active ?? e.is_active)} onChange={(ev) => setDraft({ ...draft, is_active: ev.target.value === 'true' })}>
                            <option value="true">Active</option>
                            <option value="false">Inactive</option>
                          </select>
                        </label>
                      </div>
                      <div className="field">
                        <span id={`skills-${e.id}`}>Skills (exactly {REQUIRED_SKILLS})</span>
                        <SkillPicker id={`skills-${e.id}`} value={draft.skills ?? e.skills} onChange={(skills) => setDraft({ ...draft, skills })} />
                        {skillsHelp(draft.skills ?? e.skills) && <span className="help">{skillsHelp(draft.skills ?? e.skills)}</span>}
                      </div>
                      <div className="field">
                        <span id={`home-${e.id}`}>Home buildings</span>
                        <BuildingPicker id={`home-${e.id}`} buildings={dir.buildings}
                          value={draft.home_building_ids ?? e.home_building_ids ?? []}
                          onChange={(home_building_ids) => setDraft({ ...draft, home_building_ids })} />
                      </div>
                      <div className="actions">
                        <button type="button" className="btn" onClick={() => setEditing(null)}>Cancel</button>
                        <button type="button" className="btn primary" onClick={() => save(e)}
                          disabled={busy || !Object.keys(draft).length || (draft.skills && draft.skills.length !== REQUIRED_SKILLS)}>Save</button>
                      </div>
                    </div>
                  </td>
                </tr>
              ) : (
                <tr key={e.id}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{e.user.full_name}</div>
                    <div className="tiny muted">{e.user.email}{e.phone ? ` · ${e.phone}` : ''}</div>
                  </td>
                  <td><Availability value={e.availability} /></td>
                  <td>
                    <span className="chips" style={{ gap: 4 }}>
                      {e.skills.map((s) => <span key={s} className="skill">{s}</span>)}
                      {e.skills.length < REQUIRED_SKILLS && <span className="chip c-esc">Needs {REQUIRED_SKILLS} skills</span>}
                    </span>
                  </td>
                  <td className="nowrap">{teamName(e.team_id) || <span className="muted small">None</span>}</td>
                  <td className="n">{e.workload}</td>
                  <td>{e.is_active ? 'Active' : <span className="muted">Inactive</span>}</td>
                  <td className="n"><button type="button" className="btn sm" onClick={() => { setEditing(e.id); setDraft({}); setError(null); }}>Edit</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {dir.engineers.length === 0 && <div className="empty">No engineers yet.</div>}
      </section>
    </>
  );
}
