import { useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { Icon } from '../components/ui';

const ACME_EMAIL = /^[^@\s]+@acme\.inc$/i;
const ANY_EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// Local seed accounts (backend facility/seed.py); listed only in development builds.
const PREVIEW_ACCOUNTS = [
  ['Employee', 'employee@acme.inc', 'My tickets'],
  ['Engineer', 'engineer@acme.inc', 'My queue'],
  ['Admin', 'admin@acme.inc', 'Triage and team'],
];

function AuthAside({ title, children, footer }) {
  return (
    <aside className="auth-aside">
      <div className="brand" style={{ color: '#fff' }}><span className="mark">A</span>ACME Incidents</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24, maxWidth: 460 }}>
        <h1>{title}</h1>
        {children}
      </div>
      <div className="small" style={{ color: 'var(--faint)' }}>{footer}</div>
    </aside>
  );
}

function passwordChecks(password) {
  return [
    ['At least 10 characters', password.length >= 10],
    ['At most 72 bytes', password.length > 0 && new TextEncoder().encode(password).length <= 72],
    ['Contains a letter', /[A-Za-z]/.test(password)],
    ['Contains a digit', /\d/.test(password)],
  ];
}

/** Where to go after signing in: the in-app page the user was heading to (state.from), else '/'. */
function returnPath(from) {
  const path = typeof from === 'string' ? from : `${from?.pathname || ''}${from?.search || ''}`;
  // Same-app paths only ('//host' is another site), and never back to the auth pages.
  if (!path.startsWith('/') || path.startsWith('//') || /^\/(login|register|change-password)(\/|\?|$)/.test(path)) return '/';
  return path;
}

function Rules({ password, id }) {
  return (
    <div id={id} style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '6px 12px', marginTop: -8 }}>
      {passwordChecks(password).map(([label, ok]) => (
        <span key={label} className={`rule ${ok ? 'ok' : 'no'}`}><i aria-hidden="true">{ok ? '✓' : '·'}</i>{label}</span>
      ))}
    </div>
  );
}

export function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const from = useLocation().state?.from;
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [tried, setTried] = useState(false);
  const [authError, setAuthError] = useState('');
  const [busy, setBusy] = useState(false);
  // A temporary password goes through change-password first, which then continues to `from`.
  const next = (account) => (account.must_change_password
    ? ['/change-password', { replace: true, state: { from } }]
    : [returnPath(from), { replace: true }]);

  if (user) {
    const [to, options] = next(user);
    return <Navigate to={to} {...options} />;
  }

  const emailErr = !tried ? '' : !email.trim() ? 'Enter your work email.' : ANY_EMAIL.test(email.trim()) ? '' : 'Enter an email like name@acme.inc.';
  const passErr = tried && !password ? 'Enter your password.' : '';

  const submit = async (event) => {
    event.preventDefault();
    setTried(true);
    if (!email.trim() || !password || !ANY_EMAIL.test(email.trim())) return;
    setBusy(true);
    setAuthError('');
    try {
      const session = await login(email.trim(), password);
      navigate(...next(session.user));
    } catch (error) {
      setAuthError(error.status === 401
        ? 'Email or password is incorrect, or the account is temporarily locked. After 5 failed tries, wait 15 minutes.'
        : error.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <AuthAside title="Report it once. Follow it until it's fixed." footer="For ACME employees with an @acme.inc address.">
        <div className="auth-steps">
          <div><b>1</b><span>Tell facilities what's broken and where: building, floor and seat.</span></div>
          <div><b>2</b><span>See who's assigned, what's blocking it and every update.</span></div>
          <div><b>3</b><span>Confirm the fix yourself before the ticket closes.</span></div>
        </div>
      </AuthAside>
      <main className="auth-main">
        <div className="auth-box">
          <div>
            <h2>Sign in</h2>
            <p className="muted" style={{ margin: '6px 0 0', fontSize: 14 }}>Use your work email and password.</p>
          </div>
          {authError && <div className="alert" role="alert">{authError}</div>}
          <form style={{ display: 'flex', flexDirection: 'column', gap: 18 }} onSubmit={submit} noValidate>
            <label className="field" htmlFor="login-email">Work email
              <input id="login-email" className={`input${emailErr ? ' bad' : ''}`} type="email" autoComplete="username"
                placeholder="jamie.chen@acme.inc" value={email}
                onChange={(e) => { setEmail(e.target.value); setAuthError(''); }} />
              {emailErr && <span className="err">{emailErr}</span>}
            </label>
            <label className="field" htmlFor="login-pass">
              <span className="split" style={{ alignItems: 'baseline' }}>
                <span>Password</span>
                <span className="lite">Forgot it? Ask a facilities admin.</span>
              </span>
              <span style={{ position: 'relative', display: 'block' }}>
                <input id="login-pass" className={`input${passErr ? ' bad' : ''}`} type={show ? 'text' : 'password'}
                  autoComplete="current-password" value={password} style={{ paddingRight: 72 }}
                  onChange={(e) => { setPassword(e.target.value); setAuthError(''); }} />
                <button type="button" className="linkbtn" aria-pressed={show} onClick={() => setShow(!show)}
                  style={{ position: 'absolute', right: 4, top: 4, minWidth: 60 }}>{show ? 'Hide' : 'Show'}</button>
              </span>
              {passErr && <span className="err">{passErr}</span>}
            </label>
            <button type="submit" className="btn primary block" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
          </form>
          <p style={{ margin: 0, fontSize: 14, color: 'var(--ink-2)' }}>
            New here? <Link to="/register" style={{ fontWeight: 500 }}>Create an account</Link>
          </p>
          {import.meta.env.DEV && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 20, borderTop: '1px solid var(--line)' }}>
              <div className="navlabel" style={{ padding: 0 }}>Local seed accounts</div>
              {PREVIEW_ACCOUNTS.map(([role, address, lands]) => (
                <button key={address} type="button" className="btn" style={{ justifyContent: 'space-between', minHeight: 52, height: 'auto', padding: '8px 12px', textAlign: 'left' }}
                  onClick={() => setEmail(address)}>
                  <span><strong style={{ fontWeight: 500 }}>{role}</strong><br /><span className="mono muted">{address}</span></span>
                  <span className="small muted">{lands}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export function Register() {
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' });
  const [tried, setTried] = useState(false);
  const [serverError, setServerError] = useState(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (key) => (e) => { setForm({ ...form, [key]: e.target.value }); setServerError(null); };

  const email = form.email.trim();
  const passOk = passwordChecks(form.password).every(([, ok]) => ok);
  const nameErr = tried && form.name.trim().length < 2 ? 'Enter your full name.' : '';
  const emailErr = !tried ? '' : !email ? 'Enter your work email.' : ACME_EMAIL.test(email) ? '' : 'Use your @acme.inc work email.';
  const confirmErr = tried && form.confirm !== form.password ? 'Passwords don’t match.' : '';

  const submit = async (event) => {
    event.preventDefault();
    setTried(true);
    if (form.name.trim().length < 2 || !ACME_EMAIL.test(email) || !passOk || form.confirm !== form.password) return;
    setBusy(true);
    try {
      await api.register({ full_name: form.name.trim(), email, password: form.password });
      setDone(true);
    } catch (error) {
      setServerError(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <AuthAside title="Your account is for reporting and tracking." footer="Only @acme.inc addresses can register.">
        <p style={{ margin: 0, fontSize: 15, lineHeight: 1.6, color: 'var(--ctl)' }}>
          Everyone starts as an employee and can report incidents and follow their own tickets.
          Facilities admins grant engineer or admin access.
        </p>
      </AuthAside>
      <main className="auth-main">
        <div className="auth-box">
          {done ? (
            <div className="success">
              <span style={{ color: 'var(--green)' }}><Icon name="check" size={32} /></span>
              <div>
                <h2 style={{ fontSize: 22 }}>Account created</h2>
                <p style={{ margin: '6px 0 0', fontSize: 14 }}>Sign in with {email} to report your first incident.</p>
              </div>
              <Link className="btn primary" to="/login" style={{ alignSelf: 'flex-start' }}>Sign in</Link>
            </div>
          ) : (
            <>
              <div>
                <h2>Create your account</h2>
                <p className="muted" style={{ margin: '6px 0 0', fontSize: 14 }}>Takes under a minute. You'll sign in afterwards.</p>
              </div>
              {serverError && (
                <div className="alert" role="alert">
                  {serverError.status === 409
                    ? <span>An account with this email already exists. <Link to="/login">Sign in instead</Link></span>
                    : serverError.detail}
                </div>
              )}
              <form style={{ display: 'flex', flexDirection: 'column', gap: 18 }} onSubmit={submit} noValidate>
                <label className="field" htmlFor="reg-name">Full name
                  <input id="reg-name" className={`input${nameErr ? ' bad' : ''}`} autoComplete="name" placeholder="Jamie Chen" value={form.name} onChange={set('name')} />
                  {nameErr && <span className="err">{nameErr}</span>}
                </label>
                <label className="field" htmlFor="reg-email">Work email
                  <input id="reg-email" className={`input${emailErr ? ' bad' : ''}`} type="email" autoComplete="email" placeholder="jamie.chen@acme.inc" value={form.email} onChange={set('email')} />
                  {emailErr && <span className="err">{emailErr}</span>}
                </label>
                <label className="field" htmlFor="reg-pass">Password
                  <input id="reg-pass" className={`input${tried && !passOk ? ' bad' : ''}`} type="password" autoComplete="new-password"
                    value={form.password} onChange={set('password')} aria-describedby="reg-rules" />
                </label>
                <Rules password={form.password} id="reg-rules" />
                <label className="field" htmlFor="reg-confirm">Confirm password
                  <input id="reg-confirm" className={`input${confirmErr ? ' bad' : ''}`} type="password" autoComplete="new-password" value={form.confirm} onChange={set('confirm')} />
                  {confirmErr && <span className="err">{confirmErr}</span>}
                </label>
                <button type="submit" className="btn primary block" disabled={busy}>{busy ? 'Creating…' : 'Create account'}</button>
              </form>
              <p style={{ margin: 0, fontSize: 14, color: 'var(--ink-2)' }}>
                Already have an account? <Link to="/login" style={{ fontWeight: 500 }}>Sign in</Link>
              </p>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

export function ChangePassword() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const from = useLocation().state?.from;
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!user) return <Navigate to="/login" replace />;
  const ok = passwordChecks(next).every(([, pass]) => pass);

  const submit = async (event) => {
    event.preventDefault();
    if (!current || !ok) { setError('Enter your current password and a new password that meets every rule.'); return; }
    setBusy(true);
    try {
      await api.changePassword({ current_password: current, new_password: next });
      navigate(returnPath(from), { replace: true });
    } catch (err) {
      // A wrong current password is a 422, so the session stays signed in.
      setError(err.code === 'invalid_current_password' ? 'Your current password is incorrect.' : err.detail);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <AuthAside title="Choose your own password." footer={`Signed in as ${user.email}`}>
        <p style={{ margin: 0, fontSize: 15, lineHeight: 1.6, color: 'var(--ctl)' }}>
          {user.must_change_password
            ? 'Your account was created with a temporary password. Replace it before you continue.'
            : 'Changing your password signs out your other sessions.'}
        </p>
      </AuthAside>
      <main className="auth-main">
        <form className="auth-box" onSubmit={submit} noValidate>
          <h2>Change password</h2>
          {error && <div className="alert" role="alert">{error}</div>}
          <label className="field" htmlFor="cp-current">Current password
            <input id="cp-current" className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </label>
          <label className="field" htmlFor="cp-new">New password
            <input id="cp-new" className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} aria-describedby="cp-rules" />
          </label>
          <Rules password={next} id="cp-rules" />
          <button type="submit" className="btn primary block" disabled={busy}>Save password</button>
          <button type="button" className="btn block" onClick={() => logout().finally(() => navigate('/login'))}>Sign out</button>
        </form>
      </main>
    </div>
  );
}
