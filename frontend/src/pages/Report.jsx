import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, imageType } from '../api';
import { useAuth } from '../auth';
import { useDirectory } from '../directory';
import { duration, statusLabel } from '../format';
import { useLoad } from '../hooks';
import { Icon } from '../components/ui';

const MAX_IMAGES = 5;
const MAX_BYTES = 5 * 1024 * 1024;

/** Recent open tickets on the same floor and category, offered before submitting. */
function Suggestions({ categoryId, floorId }) {
  const [followed, setFollowed] = useState({});
  const [error, setError] = useState('');
  const list = useLoad(
    () => (categoryId && floorId ? api.similar(categoryId, floorId) : Promise.resolve({ items: [] })),
    [categoryId, floorId],
  );
  const items = list.data?.items || [];
  if (!items.length) return null;
  const follow = async (item) => {
    setError('');
    try {
      await api.follow(item.id);
      setFollowed({ ...followed, [item.id]: true });
    } catch (err) {
      setError(err.detail);
    }
  };
  return (
    <section className="card pad banner warn" aria-labelledby="similar-h" style={{ display: 'block' }}>
      <h2 id="similar-h" style={{ fontSize: 16 }}>Is it one of these?</h2>
      <p className="small muted" style={{ margin: '4px 0 12px' }}>
        Open tickets on this floor in the same category from the last 7 days. Follow one to get its updates
        instead of reporting it again.
      </p>
      {error && <div className="alert" role="alert">{error}</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.map((item) => {
          const on = item.following || followed[item.id];
          return (
            <div className="suggest" key={item.id}>
              <div style={{ flex: '1 1 220px', minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 500 }}>{item.title}</div>
                <div className="tiny muted">
                  {item.number} · {statusLabel(item.status)} · {item.location.floor_name} · reported {duration(item.age_seconds)} ago
                </div>
              </div>
              <button type="button" className={`btn sm${on ? '' : ' primary'}`} disabled={on} onClick={() => follow(item)}>
                {on ? 'Following' : 'Follow'}
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}

const PRIORITIES = [
  ['Critical', 'var(--red)', 'Safety risk or the whole floor is affected.'],
  ['High', 'var(--warn-ink)', 'Blocks a team or a booked room today.'],
  ['Medium', 'var(--blue-dark)', 'Annoying but there is a workaround.'],
  ['Low', 'var(--low-ink)', 'Cosmetic, or can wait a week.'],
];

const EMPTY = { title: '', description: '', category_id: '', building_id: '', floor_id: '', seat_id: '', priority: 'Medium' };

/** Selectable categories: children, or top-level categories without active children. */
function categoryOptions(categories) {
  const parents = categories.filter((c) => !c.parent_id);
  const options = [];
  for (const parent of parents) {
    const children = categories.filter((c) => c.parent_id === parent.id);
    if (!children.length) options.push({ id: parent.id, label: parent.name, parent: parent.name, name: parent.name });
    for (const child of children) {
      options.push({ id: child.id, label: `${parent.name} › ${child.name}`, parent: parent.name, name: child.name });
    }
  }
  return options;
}

/** Questions to prompt the reporter, keyed by top-level category name. */
const TEMPLATE_QUESTIONS = {
  'IT and AV equipment': ['Device (type, model or asset tag):', 'What is happening:', 'Error message (if any):', 'What I already tried (restart, other cable, etc.):'],
  'Network and Wi-Fi': ['Wired or Wi-Fi (network name):', 'What is happening:', 'Affects only me or others nearby:', 'What I already tried:'],
  Furniture: ['Item (desk, chair, cabinet...):', 'What is broken or needed:', 'Is it safe to use right now:'],
  'HVAC and temperature': ['What I notice (too hot, too cold, smell, noise):', 'Area affected (my desk, whole room, whole floor):', 'Since when:'],
  'Electrical and lighting': ['What is affected (light, outlet, fixture):', 'Exact spot (above desk, near window...):', 'Any sparks, burning smell or heat (yes/no):'],
  'Plumbing and restrooms': ['Which restroom or kitchen:', 'What is happening (leak, clog, missing supplies):', 'Is water spreading (yes/no):'],
  Cleaning: ['What needs cleaning:', 'Exact spot:', 'Is it a slip or health hazard (yes/no):'],
  'Access and security': ['Door, reader or item involved:', 'What is happening:', 'Is anyone locked in or out right now (yes/no):'],
};
const DEFAULT_QUESTIONS = ['What is happening:', 'Since when:', 'What I already tried:'];

function descriptionTemplate(option) {
  const questions = TEMPLATE_QUESTIONS[option.parent] ?? DEFAULT_QUESTIONS;
  const issue = option.name === option.parent ? option.name : `${option.parent} - ${option.name}`;
  return [`Issue: ${issue}`, ...questions].join('\n');
}

export default function Report() {
  const dir = useDirectory();
  const auth = useAuth();
  const [form, setForm] = useState(EMPTY);
  const [images, setImages] = useState([]);
  const [uploads, setUploads] = useState([]);
  const [saveSeat, setSaveSeat] = useState(false);
  const [prefilled, setPrefilled] = useState(false);
  const [tried, setTried] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(null);
  // Bumped per submission and by "Report another", so a previous report's uploads stop updating this form.
  const runId = useRef(0);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const categories = useMemo(() => categoryOptions(dir.categories), [dir.categories]);

  // Pre-fill the reporter's default seat once the (active-only) location tree has loaded.
  // A seat that has since been deactivated is not in the tree, so it is skipped.
  const defaultSeat = auth.user?.default_seat_id;
  useEffect(() => {
    if (prefilled || !defaultSeat || !dir.buildings.length) return;
    for (const b of dir.buildings) {
      for (const f of b.floors || []) {
        if ((f.seats || []).some((seat) => seat.id === defaultSeat)) {
          // eslint-disable-next-line react-hooks/set-state-in-effect
          setForm((current) => (current.building_id ? current
            : { ...current, building_id: b.id, floor_id: f.id, seat_id: defaultSeat }));
        }
      }
    }
    setPrefilled(true);
  }, [prefilled, defaultSeat, dir.buildings]);
  const building = dir.buildings.find((b) => b.id === form.building_id);
  const floor = building?.floors.find((f) => f.id === form.floor_id);

  const tLen = form.title.trim().length;
  const dLen = form.description.trim().length;
  const tBad = tLen < 5 || tLen > 120;
  const selected = categories.find((c) => c.id === form.category_id);
  const template = selected ? descriptionTemplate(selected) : '';
  const dUnchanged = !!template && form.description.trim() === template.trim();
  const dBad = dLen < 10 || dLen > 5000 || dUnchanged;

  const chooseCategory = (id) => setForm((f) => {
    const prev = categories.find((c) => c.id === f.category_id);
    const next = categories.find((c) => c.id === id);
    // Only replace the description if it is empty or still the previous untouched template.
    const untouched = !f.description.trim() || (prev && f.description === descriptionTemplate(prev));
    const description = untouched ? (next ? descriptionTemplate(next) : '') : f.description;
    return { ...f, category_id: id, description };
  });
  const show = (bad) => tried && bad;

  const addImages = (event) => {
    const picked = [...event.target.files];
    event.target.value = '';
    const bad = picked.find((file) => !imageType(file) || file.size > MAX_BYTES);
    if (bad) {
      setError(`${bad.name}: use a JPEG, PNG, WebP or HEIC image of at most 5 MB.`);
      return;
    }
    setError('');
    setImages((current) => [...current, ...picked].slice(0, MAX_IMAGES));
  };

  /** Images need the incident ID, so they upload right after the ticket is saved. */
  const uploadAll = async (incidentId, files, run) => {
    const results = [];
    for (const file of files) {
      try {
        await api.uploadAttachment(incidentId, file);
        results.push({ name: file.name, ok: true });
      } catch (err) {
        results.push({ name: file.name, ok: false, message: err.detail || err.message });
      }
      // The rest still attach to that ticket, but the form has moved on.
      if (runId.current !== run) return;
      setUploads([...results]);
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    setTried(true);
    if (tBad || dBad || !form.category_id || !form.building_id) return;
    runId.current += 1;
    const run = runId.current;
    setBusy(true);
    setError('');
    try {
      const incident = await api.createIncident({
        title: form.title.trim(),
        description: form.description.trim(),
        category_id: form.category_id,
        building_id: form.building_id,
        floor_id: form.floor_id || undefined,
        seat_id: form.seat_id || undefined,
        requested_priority: form.priority,
      });
      setCreated(incident);
      if (saveSeat && form.seat_id && form.seat_id !== defaultSeat) {
        api.updateMe({ default_seat_id: form.seat_id }).then(() => auth.refreshUser?.()).catch(() => {});
      }
      if (images.length) await uploadAll(incident.id, images, run);
    } catch (err) {
      setError(err.detail);
    } finally {
      if (runId.current === run) setBusy(false);
    }
  };

  return (
    <div className="narrow" style={{ display: 'flex', flexDirection: 'column', gap: 20, margin: '0 auto' }}>
      <Link to="/" className="back"><Icon name="back" size={16} />My tickets</Link>
      <div className="page-head">
        <div>
          <h1>Report an incident</h1>
          <p>Tell facilities what's wrong and where. You can add notes after it's created.</p>
        </div>
      </div>

      {created ? (
        <section className="card banner ok" role="status" style={{ padding: 24, background: 'var(--good-bg)', alignItems: 'flex-start' }}>
          <span style={{ color: 'var(--green)' }}><Icon name="check" size={26} /></span>
          <div style={{ flex: '1 1 300px' }}>
            <h2 style={{ fontSize: 18, color: 'var(--green)' }}>{created.number} created</h2>
            <p style={{ margin: '6px 0 14px', fontSize: 14 }}>
              {created.routing?.status === 'assigned'
                ? `Assigned to ${created.routing.assignee.full_name}, ${created.routing.team.name}. You'll see updates on the ticket page.`
                : 'An admin will assign an engineer. You\'ll see updates on the ticket page.'}
            </p>
            {uploads.length > 0 && (
              <ul className="small" style={{ margin: '0 0 14px', paddingLeft: 18 }}>
                {uploads.map((u) => (
                  <li key={u.name} style={u.ok ? undefined : { color: 'var(--red)' }}>
                    {u.name}: {u.ok ? 'attached' : `not attached (${u.message}). You can add it again on the ticket page.`}
                  </li>
                ))}
              </ul>
            )}
            {images.length > uploads.length && <p className="small muted">Uploading images…</p>}
            <div className="actions">
              <Link className="btn primary" to={`/incidents/${created.id}`}>View ticket</Link>
              <button type="button" className="btn" onClick={() => {
                runId.current += 1;
                setForm(EMPTY); setTried(false); setCreated(null); setImages([]); setUploads([]); setPrefilled(false);
                setBusy(false);
              }}>Report another</button>
            </div>
          </div>
        </section>
      ) : (
        <form className="card" style={{ padding: 28, display: 'flex', flexDirection: 'column', gap: 22 }} onSubmit={submit} noValidate>
          {error && <div className="alert" role="alert">{error}</div>}
          <div className="field">
            <label htmlFor="r-title">Title</label>
            <input id="r-title" className={`input${show(tBad) ? ' bad' : ''}`} value={form.title} maxLength={120}
              onChange={(e) => set('title', e.target.value)} placeholder="e.g. Projector won't turn on" aria-describedby="r-title-help" />
            <div id="r-title-help" className="split">
              <span className={show(tBad) ? 'err' : 'help'}>{show(tBad) ? 'Title must be 5 to 120 characters.' : '5 to 120 characters.'}</span>
              <span className="help">{tLen}/120</span>
            </div>
          </div>

          <div className="field">
            <label htmlFor="r-cat">Category</label>
            <select id="r-cat" className={`input${show(!form.category_id) ? ' bad' : ''}`} value={form.category_id} onChange={(e) => chooseCategory(e.target.value)}>
              <option value="">Choose a category</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
            {show(!form.category_id) && <span className="err">Choose a category.</span>}
          </div>

          <div className="field">
            <label htmlFor="r-desc">Description</label>
            <textarea id="r-desc" className={`input${show(dBad) ? ' bad' : ''}`} value={form.description} maxLength={5000}
              rows={template ? Math.max(5, template.split('\n').length + 1) : undefined}
              onChange={(e) => set('description', e.target.value)} aria-describedby="r-desc-help"
              placeholder="Choose a category to get a starter template, or describe what happened, what you already tried, and anything the engineer should know." />
            <div id="r-desc-help" className="split">
              <span className={show(dBad) ? 'err' : 'help'}>
                {show(dBad)
                  ? (dUnchanged ? 'Fill in the template with your details.' : 'Description must be at least 10 characters.')
                  : (template ? 'Fill in each line of the template. At least 10 characters.' : 'At least 10 characters.')}
              </span>
              <span className="help">{dLen}/5,000</span>
            </div>
          </div>

          <fieldset style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <legend>Location</legend>
            <div className="grid" style={{ '--min': '180px', gap: 12 }}>
              <div className="field">
                <label htmlFor="r-b" className="lite">Building</label>
                <select id="r-b" className={`input${show(!form.building_id) ? ' bad' : ''}`} value={form.building_id}
                  onChange={(e) => setForm((f) => ({ ...f, building_id: e.target.value, floor_id: '', seat_id: '' }))}>
                  <option value="">Choose a building</option>
                  {dir.buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="r-f" className="lite">Floor (optional)</label>
                <select id="r-f" className="input" value={form.floor_id} disabled={!building}
                  onChange={(e) => setForm((f) => ({ ...f, floor_id: e.target.value, seat_id: '' }))}>
                  <option value="">Whole building</option>
                  {building?.floors.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="r-s" className="lite">Seat or room (optional)</label>
                <select id="r-s" className="input" value={form.seat_id} disabled={!floor} onChange={(e) => set('seat_id', e.target.value)}>
                  <option value="">No specific seat</option>
                  {floor?.seats.map((s) => <option key={s.id} value={s.id}>{s.code}</option>)}
                </select>
              </div>
            </div>
            <span className={show(!form.building_id) ? 'err' : 'help'}>
              {show(!form.building_id) ? 'Choose a building.' : 'Floors and seats only list active locations in the selected building.'}
            </span>
            {form.seat_id && form.seat_id !== defaultSeat && (
              <label className="small" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input type="checkbox" checked={saveSeat} onChange={(e) => setSaveSeat(e.target.checked)} />
                Use this seat as my default next time
              </label>
            )}
          </fieldset>

          <Suggestions categoryId={form.category_id} floorId={form.floor_id} />

          <div className="field">
            <label htmlFor="r-img">Photos (optional, up to {MAX_IMAGES}, 5 MB each)</label>
            <input id="r-img" type="file" accept="image/*" multiple
              disabled={images.length >= MAX_IMAGES} onChange={addImages} />
            {images.length > 0 && (
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                {images.map((file, index) => (
                  <li key={`${file.name}-${index}`}>
                    {file.name} · {Math.max(1, Math.round(file.size / 1024))} KB{' '}
                    <button type="button" className="linkbtn" onClick={() => setImages(images.filter((_, i) => i !== index))}>Remove</button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <fieldset>
            <legend>How urgent is it?</legend>
            <div className="grid" style={{ '--min': '160px', gap: 10 }}>
              {PRIORITIES.map(([value, color, help]) => (
                <button key={value} type="button" className={`choice${form.priority === value ? ' on' : ''}`}
                  aria-pressed={form.priority === value} onClick={() => set('priority', value)}>
                  <span style={{ fontSize: 14, fontWeight: 700, color }}>{value}</span>
                  <span className="tiny muted" style={{ lineHeight: 1.4 }}>{help}</span>
                </button>
              ))}
            </div>
            <p className="help" style={{ margin: '10px 0 0' }}>This is your requested priority. An admin sets the working priority during triage.</p>
          </fieldset>

          <div className="actions" style={{ justifyContent: 'flex-end', borderTop: '1px solid var(--line-2)', paddingTop: 20 }}>
            <Link className="btn" to="/">Cancel</Link>
            <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Submitting…' : 'Submit incident'}</button>
          </div>
        </form>
      )}
    </div>
  );
}
