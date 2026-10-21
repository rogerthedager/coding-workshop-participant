import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { ErrorBox, Loading } from '../components/ui';
import { useLoad } from '../hooks';
import { Pager } from './MyTickets';
import './Admin.css';

// Employee is implicit for everyone; these are the additive roles.
const ROLES = [['engineer', 'Engineer'], ['lead', 'Lead'], ['admin', 'Admin']];
const PAGE_SIZE = 25;

/** Plain-language text for the conflicts PATCH /users/{id} can return. */
function explain(error) {
  const count = error.details?.affected_count;
  switch (error.code) {
    case 'last_admin':
      return 'This is the last active admin. Make someone else an admin first.';
    case 'has_assigned_incidents':
      return `Reassign their ${count ? `${count} ` : ''}open tickets first, then try again.`;
    case 'team_needs_lead':
      return 'They lead a team. Give that team a new lead in Teams and routing first.';
    case 'engineer_profile_required':
      return 'Engineers need a profile. Add or reactivate them on the Engineers page instead.';
    default:
      return count ? `${error.detail} (${count} tickets)` : error.detail;
  }
}

function UserRow({ user, self, onSaved }) {
  const auth = useAuth();
  const navigate = useNavigate();
  const [draft, setDraft] = useState({ roles: user.roles, is_active: user.is_active });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dirty = draft.is_active !== user.is_active
    || [...draft.roles].sort().join() !== [...user.roles].sort().join();
  const toggleRole = (role) => setDraft({
    ...draft,
    roles: draft.roles.includes(role) ? draft.roles.filter((r) => r !== role) : [...draft.roles, role],
  });
  const save = async () => {
    const deactivating = user.is_active && !draft.is_active;
    const droppingAdmin = user.roles.includes('admin') && !draft.roles.includes('admin');
    // Changing your own access takes effect at once, so make sure it is intended.
    if (self && deactivating && !window.confirm(
      'Deactivate your own account? You will be signed out now and cannot sign in again until another admin reactivates you.',
    )) return;
    if (self && !deactivating && droppingAdmin && !window.confirm(
      'Remove your own admin role? You will lose access to this page and the other admin pages as soon as you save.',
    )) return;
    setBusy(true);
    setError('');
    try {
      const saved = await api.updateUser(user.id, draft);
      if (self && deactivating) {
        // The server has revoked this session; finish signing out cleanly.
        await auth.logout().catch(() => {});
        navigate('/login', { replace: true });
        return;
      }
      onSaved(`${saved.full_name} saved.`);
      // Refresh our own roles so the navigation and route guards follow the change.
      if (self) auth.refreshUser().catch(() => {});
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="adm-item">
      <div className="adm-row">
        <span className="adm-name">{user.full_name}{self ? ' (you)' : ''}</span>
        <span className="adm-meta">{user.email}</span>
        <span className={`chip ${user.is_active ? 'adm-on' : 'adm-off'}`}>{user.is_active ? 'Active' : 'Inactive'}</span>
        {user.must_change_password && <span className="chip c-esc">Temporary password</span>}
      </div>
      <div className="adm-row">
        <div className="chips" role="group" aria-label={`Roles for ${user.full_name}`}>
          {ROLES.map(([role, label]) => (
            <label key={role} className="adm-check">
              <input type="checkbox" checked={draft.roles.includes(role)} onChange={() => toggleRole(role)} />
              {label}
            </label>
          ))}
          <label className="adm-check">
            <input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} />
            Active
          </label>
        </div>
        <div className="adm-push">
          {dirty && (
            <button type="button" className="btn sm" disabled={busy}
              onClick={() => { setDraft({ roles: user.roles, is_active: user.is_active }); setError(''); }}>
              Undo
            </button>
          )}
          <button type="button" className="btn sm primary" disabled={!dirty || busy} onClick={save}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
      {error && <div className="alert" role="alert">{error}</div>}
    </li>
  );
}

/** Admin list of accounts: grant or remove roles and deactivate accounts. */
export default function Users() {
  const { user: me } = useAuth();
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [page, setPage] = useState(1);
  const [notice, setNotice] = useState('');
  const list = useLoad(() => api.users({ q: term || undefined, page, page_size: PAGE_SIZE }), [term, page]);
  const data = list.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  const search = (event) => {
    event.preventDefault();
    setTerm(q.trim());
    setPage(1);
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Users</h1>
          <p>
            Everyone is an employee. Grant lead or admin here; to make someone an engineer,
            add them on the <Link to="/engineers">Engineers</Link> page first.
          </p>
        </div>
      </div>
      {notice && <div className="notice" role="status">{notice}</div>}
      <section className="card">
        <form className="card-head filters" role="search" onSubmit={search}>
          <label style={{ flex: '1 1 260px' }}>Name or email
            <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} maxLength={120} />
          </label>
          <button type="submit" className="btn">Search</button>
          {term && <button type="button" className="linkbtn" onClick={() => { setQ(''); setTerm(''); setPage(1); }}>Clear</button>}
        </form>
        <ErrorBox error={list.error} />
        {list.loading && !data ? <Loading /> : data && (
          <>
            <ul className="adm-list">
              {data.items.map((item) => (
                // Keyed on the saved state so the row's draft resets after a reload.
                <UserRow key={`${item.id}-${item.roles.join()}-${item.is_active}`} user={item} self={item.id === me?.id}
                  onSaved={(message) => { setNotice(message); list.reload(); }} />
              ))}
            </ul>
            {data.items.length === 0 && <div className="empty">No users match.</div>}
            <Pager data={data} page={page} pages={pages} setPage={setPage} />
          </>
        )}
      </section>
    </>
  );
}
