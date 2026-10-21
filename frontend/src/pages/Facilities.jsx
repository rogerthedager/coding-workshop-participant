import { useState } from 'react';
import { api } from '../api';
import { useDirectory } from '../directory';
import { useLoad } from '../hooks';
import { ErrorBox, Loading } from '../components/ui';
import './Admin.css';

const MAX_BULK = 200;

function ActiveChip({ on }) {
  return <span className={`chip ${on ? 'adm-on' : 'adm-off'}`}>{on ? 'Active' : 'Inactive'}</span>;
}

/** Turn an API error into a sentence an admin can act on. */
function explain(err) {
  if (err?.status === 409 && err.code === 'has_open_incidents') {
    const count = err.details?.count;
    return count
      ? `${count} open ticket${count === 1 ? '' : 's'} still use${count === 1 ? 's' : ''} this location. Resolve or move them before deactivating it.`
      : 'Open tickets still use this location. Resolve or move them before deactivating it.';
  }
  if (err?.status === 409 && err.code === 'duplicate') {
    if (Array.isArray(err.details?.codes)) {
      return { text: 'Nothing was created: these seat codes already exist on this floor.', codes: err.details.codes };
    }
    // Case-insensitive match; details.field names the clashing column ('name' or 'code').
    const field = err.details?.field;
    return `That ${field === 'name' || field === 'code' ? field : 'name or code'} is already in use. Pick a different one.`;
  }
  // A raw database unique violation (e.g. a floor or category name) comes back as a generic conflict.
  if (err?.status === 409 && err.code === 'conflict' && /constraint/i.test(err.message)) {
    return 'That name or code is already in use. Pick a different one.';
  }
  return err?.detail || err?.message || String(err);
}

function seatCodes(prefix, from, to, pad) {
  const a = Number(from);
  const b = Number(to);
  const p = Number(pad);
  if (from === '' || to === '' || !Number.isInteger(a) || !Number.isInteger(b) || !Number.isInteger(p)) return null;
  if (a < 0 || b < a || p < 0 || p > 6) return null;
  const code = (n) => `${prefix}${String(n).padStart(p, '0')}`;
  return { first: code(a), last: code(b), count: b - a + 1 };
}

function SeatAdder({ floor, onDone, run, busy }) {
  const [mode, setMode] = useState('bulk');
  const [code, setCode] = useState('');
  const [bulk, setBulk] = useState({ prefix: '', from: '1', to: '', pad: '2' });
  const preview = seatCodes(bulk.prefix, bulk.from, bulk.to, bulk.pad);
  const tooMany = preview && preview.count > MAX_BULK;
  const set = (key) => (e) => setBulk({ ...bulk, [key]: e.target.value });

  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    if (mode === 'single') {
      if (!code.trim()) return;
      if (await run(() => api.createSeat(floor.id, { code: code.trim() }), `Seat ${code.trim()} added to ${floor.name}.`)) onDone();
      return;
    }
    if (!preview || tooMany) return;
    const body = { prefix: bulk.prefix, from: Number(bulk.from), to: Number(bulk.to), pad: Number(bulk.pad) };
    if (await run(() => api.bulkSeats(floor.id, body), `${preview.count} seats added to ${floor.name}.`)) onDone();
  };

  return (
    <form className="adm-form" onSubmit={submit}>
      <div className="adm-row">
        <h3>Add seats to {floor.name}</h3>
        <div className="pill-group" role="group" aria-label="How to add seats">
          <button type="button" className={mode === 'bulk' ? 'on' : ''} aria-pressed={mode === 'bulk'} onClick={() => setMode('bulk')}>Range</button>
          <button type="button" className={mode === 'single' ? 'on' : ''} aria-pressed={mode === 'single'} onClick={() => setMode('single')}>Single</button>
        </div>
      </div>
      {mode === 'single' ? (
        <div className="filters">
          <label>Seat code
            <input className="input" value={code} maxLength={40} placeholder="3-A-01" onChange={(e) => setCode(e.target.value)} />
          </label>
        </div>
      ) : (
        <>
          <div className="filters">
            <label>Prefix
              <input className="input" value={bulk.prefix} maxLength={30} placeholder="3-A-" onChange={set('prefix')} />
            </label>
            <label className="adm-narrow">From
              <input className="input" type="number" min={0} max={99999} value={bulk.from} onChange={set('from')} />
            </label>
            <label className="adm-narrow">To
              <input className="input" type="number" min={0} max={99999} value={bulk.to} onChange={set('to')} />
            </label>
            <label className="adm-narrow">Zero-pad
              <input className="input" type="number" min={0} max={6} value={bulk.pad} onChange={set('pad')} />
            </label>
          </div>
          <div className={`adm-preview${tooMany ? ' err' : ''}`} aria-live="polite">
            {!preview && 'Enter a range where “from” is not greater than “to”.'}
            {preview && !tooMany && (
              <>Preview: <span className="mono">{preview.first}</span>
                {preview.count > 1 && <> … <span className="mono">{preview.last}</span></>}
                {' '}({preview.count} seat{preview.count === 1 ? '' : 's'})</>
            )}
            {tooMany && `${preview.count} seats — at most ${MAX_BULK} per batch. Split the range.`}
          </div>
        </>
      )}
      <div className="actions">
        <button type="button" className="btn" onClick={onDone}>Cancel</button>
        <button type="submit" className="btn primary"
          disabled={busy || (mode === 'single' ? !code.trim() : !preview || tooMany)}>
          {mode === 'single' ? 'Add seat' : `Add ${preview && !tooMany ? preview.count : ''} seats`}
        </button>
      </div>
    </form>
  );
}

function FloorRow({ floor, buildingActive, run, busy }) {
  const [showSeats, setShowSeats] = useState(false);
  const [adding, setAdding] = useState(false);
  const [seat, setSeat] = useState(null);
  const seats = floor.seats || [];
  const activeSeats = seats.filter((s) => s.is_active).length;
  const selected = seats.find((s) => s.id === seat);

  return (
    <div className="adm-sub">
      <div className="adm-row">
        <button type="button" className="adm-toggle" aria-expanded={showSeats} onClick={() => setShowSeats(!showSeats)}>
          <span className="caret" aria-hidden="true">{showSeats ? '▾' : '▸'}</span>
          {floor.name}
        </button>
        <span className="tiny muted">Level {floor.level_number} · {activeSeats}/{seats.length} seats active</span>
        <ActiveChip on={floor.is_active} />
        <span className="adm-push">
          {floor.is_active && buildingActive && (
            <button type="button" className="btn sm" aria-expanded={adding}
              onClick={() => { setAdding(!adding); setShowSeats(true); }}>Add seats</button>
          )}
          <button type="button" className={`btn sm${floor.is_active ? ' danger' : ''}`}
            onClick={() => run(() => api.updateFloor(floor.id, { is_active: !floor.is_active }),
              `${floor.name} ${floor.is_active ? 'deactivated' : 'reactivated'}.`)}>
            {floor.is_active ? 'Deactivate' : 'Activate'}
          </button>
        </span>
      </div>
      {adding && <SeatAdder floor={floor} run={run} busy={busy} onDone={() => setAdding(false)} />}
      {showSeats && (
        seats.length === 0 ? <div className="small muted">No seats on this floor.</div> : (
          <>
            <div className="adm-seats" role="group" aria-label={`Seats on ${floor.name}; select one to change it`}>
              {seats.map((s) => (
                <button key={s.id} type="button" aria-pressed={seat === s.id}
                  title={s.is_active ? 'Active' : 'Inactive'}
                  className={`adm-seat${s.is_active ? '' : ' off'}${seat === s.id ? ' on' : ''}`}
                  onClick={() => setSeat(seat === s.id ? null : s.id)}>
                  {s.code}
                </button>
              ))}
            </div>
            {selected && (
              <div className="adm-row">
                <span className="small">Seat <span className="mono">{selected.code}</span> is {selected.is_active ? 'active' : 'inactive'}.</span>
                <button type="button" className={`btn sm${selected.is_active ? ' danger' : ''}`}
                  onClick={async () => {
                    if (await run(() => api.updateSeat(selected.id, { is_active: !selected.is_active }),
                      `Seat ${selected.code} ${selected.is_active ? 'deactivated' : 'reactivated'}.`)) setSeat(null);
                  }}>
                  {selected.is_active ? 'Deactivate seat' : 'Activate seat'}
                </button>
                <button type="button" className="btn sm" onClick={() => setSeat(null)}>Done</button>
              </div>
            )}
          </>
        )
      )}
    </div>
  );
}

function BuildingItem({ building, run, busy }) {
  const [open, setOpen] = useState(false);
  const [floorForm, setFloorForm] = useState(null);
  const floors = building.floors || [];
  const seatCount = floors.reduce((sum, f) => sum + (f.seats?.length || 0), 0);

  const addFloor = async (event) => {
    event.preventDefault();
    if (busy) return;
    const level = Number(floorForm.level);
    if (!floorForm.name.trim() || floorForm.level === '' || !Number.isInteger(level)) return;
    const ok = await run(() => api.createFloor(building.id, { name: floorForm.name.trim(), level_number: level }),
      `${floorForm.name.trim()} added to ${building.name}.`);
    if (ok) {
      setFloorForm(null);
      setOpen(true);
    }
  };

  return (
    <li className="adm-item">
      <div className="adm-row">
        <button type="button" className="adm-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          <span className="caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
          {building.name}
        </button>
        <span className="chip adm-off mono">{building.code}</span>
        <ActiveChip on={building.is_active} />
        <span className="adm-push">
          {building.is_active && (
            <button type="button" className="btn sm" aria-expanded={Boolean(floorForm)}
              onClick={() => setFloorForm(floorForm ? null : { name: '', level: '' })}>Add floor</button>
          )}
          <button type="button" className={`btn sm${building.is_active ? ' danger' : ''}`}
            onClick={() => run(() => api.updateBuilding(building.id, { is_active: !building.is_active }),
              `${building.name} ${building.is_active ? 'deactivated' : 'reactivated'}.`)}>
            {building.is_active ? 'Deactivate' : 'Activate'}
          </button>
        </span>
      </div>
      <div className="adm-meta">
        {building.address ? `${building.address} · ` : ''}{floors.length} floor{floors.length === 1 ? '' : 's'} · {seatCount} seat{seatCount === 1 ? '' : 's'}
      </div>
      {floorForm && (
        <form className="adm-form" onSubmit={addFloor}>
          <h3>Add floor to {building.name}</h3>
          <div className="filters">
            <label>Floor name
              <input className="input" value={floorForm.name} maxLength={120} placeholder="Floor 3"
                onChange={(e) => setFloorForm({ ...floorForm, name: e.target.value })} />
            </label>
            <label className="adm-narrow">Level number
              <input className="input" type="number" min={-50} max={200} value={floorForm.level}
                onChange={(e) => setFloorForm({ ...floorForm, level: e.target.value })} />
            </label>
          </div>
          <div className="actions">
            <button type="button" className="btn" onClick={() => setFloorForm(null)}>Cancel</button>
            <button type="submit" className="btn primary" disabled={busy}>Add floor</button>
          </div>
        </form>
      )}
      {open && (
        <div className="adm-nest">
          {floors.length === 0 ? <div className="small muted">No floors yet.</div>
            : floors.map((f) => <FloorRow key={f.id} floor={f} buildingActive={building.is_active} run={run} busy={busy} />)}
        </div>
      )}
    </li>
  );
}

function CategoryEditor({ category, groups, onSave, onCancel }) {
  const top = !category.parent_id;
  const [form, setForm] = useState({
    name: category.name, sort_order: String(category.sort_order), group_id: category.group_id || '',
  });
  const save = (event) => {
    event.preventDefault();
    const body = {};
    if (form.name.trim() && form.name.trim() !== category.name) body.name = form.name.trim();
    if (form.sort_order !== '' && Number(form.sort_order) !== category.sort_order) body.sort_order = Number(form.sort_order);
    if (top && (form.group_id || null) !== (category.group_id || null)) body.group_id = form.group_id || null;
    onSave(body);
  };
  return (
    <form className="adm-form" onSubmit={save}>
      <div className="filters">
        <label>Name
          <input className="input" value={form.name} maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label className="adm-narrow">Order
          <input className="input" type="number" min={0} max={10000} value={form.sort_order}
            onChange={(e) => setForm({ ...form, sort_order: e.target.value })} />
        </label>
        {top && (
          <label>Routing group
            <select className="sel" value={form.group_id} onChange={(e) => setForm({ ...form, group_id: e.target.value })}>
              <option value="">No group (admin triage)</option>
              {groups.map((g) => <option key={g.id} value={g.id}>{g.name}{g.is_active ? '' : ' (inactive)'}</option>)}
            </select>
          </label>
        )}
      </div>
      <p className="help" style={{ margin: 0 }}>Lower order numbers are listed first.{top ? '' : ' Subcategories use their parent’s group.'}</p>
      <div className="actions">
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn primary">Save</button>
      </div>
    </form>
  );
}

/** Admin maintenance of buildings, floors, seats and the ticket category tree. */
export default function Facilities() {
  const dir = useDirectory();
  const { data, error: loadError, loading, reload } = useLoad(
    () => Promise.all([api.locationTree(), api.allCategories(), api.categoryGroups()])
      .then(([tree, categories, groups]) => ({ buildings: tree.items, categories: categories.items, groups: groups.items })),
    [],
  );
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState('');
  const [newBuilding, setNewBuilding] = useState(null);
  const [newCategory, setNewCategory] = useState(null); // { parent_id, name, group_id, sort_order }
  const [editing, setEditing] = useState(null);
  // A create in flight; its submit button stays disabled so a double click cannot send it twice.
  const [busy, setBusy] = useState(false);

  if (loading && !data) return <Loading />;
  if (loadError) return <ErrorBox error={loadError} />;

  const { buildings, categories, groups } = data;
  const tops = categories.filter((c) => !c.parent_id);
  const children = (id) => categories.filter((c) => c.parent_id === id);
  const groupName = (id) => groups.find((g) => g.id === id)?.name;

  const run = async (action, message) => {
    setError(null);
    setNotice('');
    setBusy(true);
    try {
      await action();
      setNotice(message);
      reload();
      dir?.reload();
      return true;
    } catch (err) {
      setError(explain(err));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const addBuilding = async (event) => {
    event.preventDefault();
    if (busy) return;
    const { name, code, address } = newBuilding;
    if (!name.trim() || !code.trim()) {
      setError('A building needs a name and a short code.');
      return;
    }
    const ok = await run(() => api.createBuilding({ name: name.trim(), code: code.trim(), address: address.trim() || null }),
      `${name.trim()} added.`);
    if (ok) setNewBuilding(null);
  };

  const addCategory = async (event) => {
    event.preventDefault();
    if (busy) return;
    const { parent_id: parentId, name, group_id: groupId, sort_order: order } = newCategory;
    if (!name.trim()) {
      setError('Give the category a name.');
      return;
    }
    const body = { name: name.trim() };
    if (parentId) body.parent_id = parentId;
    else if (groupId) body.group_id = groupId;
    if (order !== '') body.sort_order = Number(order);
    const ok = await run(() => api.createCategory(body), `Category "${body.name}" added.`);
    if (ok) setNewCategory(null);
  };

  const toggleCategory = (c) => run(() => api.updateCategory(c.id, { is_active: !c.is_active }),
    `"${c.name}" ${c.is_active ? 'deactivated' : 'reactivated'}.`);

  const categoryForm = (parent) => (
    <form className="adm-form" onSubmit={addCategory}>
      <h3>{parent ? `New subcategory of ${parent.name}` : 'New top-level category'}</h3>
      <div className="filters">
        <label>Name
          <input className="input" value={newCategory.name} maxLength={120}
            onChange={(e) => setNewCategory({ ...newCategory, name: e.target.value })} />
        </label>
        <label className="adm-narrow">Order
          <input className="input" type="number" min={0} max={10000} value={newCategory.sort_order} placeholder="0"
            onChange={(e) => setNewCategory({ ...newCategory, sort_order: e.target.value })} />
        </label>
        {!parent && (
          <label>Routing group (optional)
            <select className="sel" value={newCategory.group_id} onChange={(e) => setNewCategory({ ...newCategory, group_id: e.target.value })}>
              <option value="">No group (admin triage)</option>
              {groups.filter((g) => g.is_active).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </label>
        )}
      </div>
      {parent && <p className="help" style={{ margin: 0 }}>Subcategories use their parent&apos;s routing group.</p>}
      <div className="actions">
        <button type="button" className="btn" onClick={() => setNewCategory(null)}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy}>Add category</button>
      </div>
    </form>
  );

  const categoryRow = (c, parent) => (
    <div className={parent ? 'adm-sub' : undefined} key={c.id}>
      <div className="adm-row">
        <span className="adm-name">{c.name}</span>
        <span className="tiny muted">#{c.sort_order}</span>
        {!parent && <span className="skill">{c.group_id ? groupName(c.group_id) || 'Unknown group' : 'No group'}</span>}
        <ActiveChip on={c.is_active} />
        <span className="adm-push">
          <button type="button" className="btn sm" aria-expanded={editing === c.id}
            onClick={() => setEditing(editing === c.id ? null : c.id)}>Edit</button>
          {!parent && c.is_active && (
            <button type="button" className="btn sm"
              onClick={() => setNewCategory({ parent_id: c.id, name: '', group_id: '', sort_order: '' })}>Add sub</button>
          )}
          <button type="button" className={`btn sm${c.is_active ? ' danger' : ''}`} onClick={() => toggleCategory(c)}>
            {c.is_active ? 'Deactivate' : 'Activate'}
          </button>
        </span>
      </div>
      {editing === c.id && (
        <CategoryEditor category={c} groups={groups} onCancel={() => setEditing(null)}
          onSave={async (body) => {
            if (!Object.keys(body).length) { setEditing(null); return; }
            if (await run(() => api.updateCategory(c.id, body), `"${body.name || c.name}" saved.`)) setEditing(null);
          }} />
      )}
    </div>
  );

  return (
    <div className="adm-stack">
      <div className="page-head">
        <div>
          <h1>Facilities</h1>
          <p>Buildings, floors and seats people report from, and the categories they choose.</p>
        </div>
      </div>

      {notice && <div className="notice" role="status">{notice}</div>}
      {error && (
        <div className="alert" role="alert">
          {typeof error === 'string' ? error : (
            <div>
              {error.text}
              <div className="adm-codes">{error.codes.join(', ')}</div>
            </div>
          )}
        </div>
      )}

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Locations</h2>
            <div className="sub">Records are never deleted; deactivate them to hide them from new tickets.</div>
          </div>
          <button type="button" className="btn primary sm"
            onClick={() => setNewBuilding(newBuilding ? null : { name: '', code: '', address: '' })}>
            {newBuilding ? 'Close' : 'Add building'}
          </button>
        </div>
        {newBuilding && (
          <form className="adm-form" onSubmit={addBuilding}>
            <h3>New building</h3>
            <div className="filters">
              <label>Name
                <input className="input" value={newBuilding.name} maxLength={120}
                  onChange={(e) => setNewBuilding({ ...newBuilding, name: e.target.value })} />
              </label>
              <label className="adm-narrow">Code
                <input className="input" value={newBuilding.code} maxLength={30} placeholder="HQ"
                  onChange={(e) => setNewBuilding({ ...newBuilding, code: e.target.value })} />
              </label>
              <label>Address (optional)
                <input className="input" value={newBuilding.address} maxLength={500}
                  onChange={(e) => setNewBuilding({ ...newBuilding, address: e.target.value })} />
              </label>
            </div>
            <div className="actions">
              <button type="button" className="btn" onClick={() => setNewBuilding(null)}>Cancel</button>
              <button type="submit" className="btn primary" disabled={busy}>Add building</button>
            </div>
          </form>
        )}
        {buildings.length === 0 ? <div className="empty">No buildings yet.</div> : (
          <ul className="adm-list">
            {buildings.map((b) => <BuildingItem key={b.id} building={b} run={run} busy={busy} />)}
          </ul>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Categories</h2>
            <div className="sub">Two levels. The routing group of a top-level category decides which team gets its tickets.</div>
          </div>
          <button type="button" className="btn primary sm"
            onClick={() => setNewCategory(newCategory && !newCategory.parent_id ? null
              : { parent_id: null, name: '', group_id: '', sort_order: '' })}>
            {newCategory && !newCategory.parent_id ? 'Close' : 'Add category'}
          </button>
        </div>
        {newCategory && !newCategory.parent_id && categoryForm(null)}
        {tops.length === 0 ? <div className="empty">No categories yet.</div> : (
          <ul className="adm-list">
            {tops.map((c) => {
              const subs = children(c.id);
              return (
                <li key={c.id} className="adm-item">
                  {categoryRow(c, null)}
                  {newCategory?.parent_id === c.id && categoryForm(c)}
                  {subs.length > 0 && (
                    <div className="adm-nest">{subs.map((s) => categoryRow(s, c))}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
