import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { api, NOTIFICATIONS_CHANGED } from '../api';
import { useAuth } from '../auth';
import { initials, roleLabel } from '../format';
import { useToast } from '../hooks';
import { BellIcon, Icon, Toast } from './ui';

const POLL_MS = 30000;

function navItems({ isAdmin, isEngineer, isLead }) {
  const items = [
    { to: '/', icon: 'list', label: 'My tickets', end: true },
    { to: '/report', icon: 'plus', label: 'Report an incident' },
    { to: '/incidents', icon: 'search', label: 'Search', end: true },
    { to: '/dashboard', icon: 'chart', label: 'Dashboard' },
  ];
  const staff = [
    isEngineer && { to: '/queue', icon: 'wrench', label: 'My queue' },
    (isEngineer || isAdmin) && { to: '/board', icon: 'overview', label: 'Board' },
    (isLead || isAdmin) && { to: '/team', icon: 'team', label: 'Team summary' },
  ].filter(Boolean);
  const admin = isAdmin
    ? [
        { to: '/triage', icon: 'filter', label: 'Triage' },
        { to: '/workload', icon: 'team', label: 'Workload' },
        { to: '/overview', icon: 'overview', label: 'Overview' },
        { to: '/engineers', icon: 'wrench', label: 'Engineers' },
        { to: '/admin/users', icon: 'team', label: 'Users' },
        { to: '/admin/teams', icon: 'team', label: 'Teams and routing' },
        { to: '/admin/facilities', icon: 'list', label: 'Facilities' },
      ]
    : [];
  return { items, staff, admin };
}

function NavItem({ item, onClick }) {
  return (
    <NavLink to={item.to} end={item.end} onClick={onClick} className={({ isActive }) => (isActive ? 'active' : '')}>
      <Icon name={item.icon} />
      {item.label}
    </NavLink>
  );
}

/**
 * Unread count, polled every 30 s while the tab is visible and immediately on focus
 * or navigation (FR-10.1).
 */
function useUnreadCount() {
  const [count, setCount] = useState(0);
  const location = useLocation();
  const check = useCallback(() => {
    if (document.visibilityState !== 'visible') return;
    api.unreadCount().then((data) => setCount(data.unread), () => {});
  }, []);
  useEffect(() => {
    check();
    const timer = window.setInterval(check, POLL_MS);
    document.addEventListener('visibilitychange', check);
    window.addEventListener('focus', check);
    // Marking notifications read (see api.js) updates the badge straight away.
    window.addEventListener(NOTIFICATIONS_CHANGED, check);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', check);
      window.removeEventListener('focus', check);
      window.removeEventListener(NOTIFICATIONS_CHANGED, check);
    };
  }, [check]);
  // Re-check after moving between pages (e.g. after reading notifications).
  useEffect(check, [check, location.pathname]);
  return count;
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled])';

/**
 * The mobile "More" sheet as a modal dialog: Escape closes it, Tab stays inside it,
 * and focus goes back to the More button when it closes.
 */
function useMoreSheet() {
  const [open, setOpen] = useState(false);
  const button = useRef(null);
  const sheet = useRef(null);
  const wasOpen = useRef(false);
  const location = useLocation();
  const here = location.pathname + location.search;
  // Any navigation closes the sheet.
  const [openedAt, setOpenedAt] = useState(here);
  if (openedAt !== here) {
    setOpenedAt(here);
    setOpen(false);
  }

  useEffect(() => {
    if (!open) {
      // Its focused control is gone; return focus to the button unless it moved elsewhere.
      if (wasOpen.current && (!document.activeElement || document.activeElement === document.body)) {
        button.current?.focus();
      }
      wasOpen.current = false;
      return undefined;
    }
    wasOpen.current = true;
    // Focus the dialog itself: focusing the search box would pop up the phone keyboard.
    sheet.current?.focus();
    const onKey = (event) => {
      if (event.key === 'Escape') {
        setOpen(false);
        button.current?.focus();
        return;
      }
      if (event.key !== 'Tab' || !sheet.current) return;
      const items = [...sheet.current.querySelectorAll(FOCUSABLE)];
      const index = items.indexOf(document.activeElement);
      const wrap = event.shiftKey ? index <= 0 : index === -1 || index === items.length - 1;
      if (items.length && wrap) {
        event.preventDefault();
        (event.shiftKey ? items.at(-1) : items[0]).focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return { open, setOpen, button, sheet };
}

/** Shows `navigate(to, { state: { notice } })` as a toast, e.g. after leaving a ticket. */
function useArrivalNotice() {
  const [toast, showToast, setToast] = useToast();
  const location = useLocation();
  const navigate = useNavigate();
  const notice = location.state?.notice;
  useEffect(() => {
    if (!notice) return;
    showToast(notice);
    // Drop it from history so a reload or Back does not repeat it.
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
  }, [notice, location.pathname, location.search, navigate, showToast]);
  return [toast, setToast];
}

export default function Layout() {
  const auth = useAuth();
  const { user } = auth;
  const navigate = useNavigate();
  const [notice, setNotice] = useArrivalNotice();
  const [params] = useSearchParams();
  const urlQ = params.get('q') || '';
  const [q, setQ] = useState(urlQ);
  // Follow ?q when it changes (links, back/forward, clearing filters).
  const [syncedQ, setSyncedQ] = useState(urlQ);
  if (urlQ !== syncedQ) {
    setSyncedQ(urlQ);
    setQ(urlQ);
  }
  const { open: more, setOpen: setMore, button: moreButton, sheet: moreSheet } = useMoreSheet();
  const unread = useUnreadCount();
  const { items, staff, admin } = navItems(auth);
  const closeMore = () => setMore(false);

  const search = (event) => {
    event.preventDefault();
    setMore(false);
    navigate(q.trim() ? `/incidents?q=${encodeURIComponent(q.trim())}` : '/incidents');
  };
  const signOut = async () => {
    await auth.logout().catch(() => {});
    navigate('/login');
  };
  const mobile = [
    { to: '/', icon: 'list', label: 'My tickets', end: true },
    auth.isAdmin
      ? { to: '/triage', icon: 'filter', label: 'Triage' }
      : auth.isEngineer
        ? { to: '/queue', icon: 'wrench', label: 'Queue' }
        : auth.isLead
          ? { to: '/team', icon: 'team', label: 'Team' }
          : { to: '/incidents', icon: 'search', label: 'Search', end: true },
    { to: '/dashboard', icon: 'chart', label: 'Dashboard' },
    { to: '/report', icon: 'plus', label: 'Report' },
  ];
  const bellLabel = unread ? `Notifications, ${unread} unread` : 'Notifications';

  return (
    <div className="app">
      <header className="topbar">
        <Link className="brand" to="/"><span className="mark">A</span>ACME Incidents</Link>
        <form className="search" role="search" onSubmit={search}>
          <Icon name="search" size={18} />
          <label htmlFor="global-search" className="sr-only">Search incidents</label>
          <input id="global-search" type="search" value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Search by INC number, title or description" />
        </form>
        <div className="who">
          <Link to="/notifications" className="btn sm bell" aria-label={bellLabel} title={bellLabel}>
            <BellIcon size={18} />
            {unread > 0 && <span className="badge" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>}
          </Link>
          <div className="who-text">
            <div style={{ fontSize: 14, fontWeight: 500 }}>{user.full_name}</div>
            <div className="tiny muted">{roleLabel(user)}</div>
          </div>
          <div className="avatar" aria-hidden="true">{initials(user.full_name)}</div>
          <button type="button" className="btn sm" onClick={signOut} aria-label="Sign out" title="Sign out">
            <Icon name="logout" size={18} />
          </button>
        </div>
      </header>
      <div className="shell">
        <nav className="nav" aria-label="Main">
          {/* Role-specific tools first; the general user links go underneath. */}
          {admin.length > 0 && <div className="navlabel">Admin</div>}
          {admin.map((item) => <NavItem key={item.to} item={item} />)}
          {staff.length > 0 && <div className="navlabel">Staff</div>}
          {staff.map((item) => <NavItem key={item.to} item={item} />)}
          {(admin.length > 0 || staff.length > 0) && <div className="navlabel">User</div>}
          {items.map((item) => <NavItem key={item.to} item={item} />)}
        </nav>
        <main className="content">
          <Outlet />
        </main>
      </div>
      <Toast toast={notice} onDone={setNotice} />
      {more && (
        // Every role-appropriate link plus search, for phones where the side nav is hidden.
        <div id="more-menu" ref={moreSheet} className="more-sheet" role="dialog" aria-modal="true"
          aria-label="All pages" tabIndex={-1}>
          <div className="more-head">
            <form className="more-search" role="search" onSubmit={search}>
              <Icon name="search" size={18} />
              <label htmlFor="mobile-search" className="sr-only">Search incidents</label>
              <input id="mobile-search" type="search" value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="Search tickets" />
            </form>
            {/* aria-modal hides the bottom bar from screen readers, so the sheet needs its own close. */}
            <button type="button" className="btn sm" onClick={closeMore}>Close</button>
          </div>
          <nav className="more-links" aria-label="All pages">
            {admin.length > 0 && <div className="navlabel">Admin</div>}
            {admin.map((item) => <NavItem key={item.to} item={item} onClick={closeMore} />)}
            {staff.length > 0 && <div className="navlabel">Staff</div>}
            {staff.map((item) => <NavItem key={item.to} item={item} onClick={closeMore} />)}
            {(admin.length > 0 || staff.length > 0) && <div className="navlabel">User</div>}
            {items.map((item) => <NavItem key={item.to} item={item} onClick={closeMore} />)}
          </nav>
        </div>
      )}
      <nav className="bottomnav" aria-label="Main (mobile)">
        {mobile.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} onClick={closeMore} className={({ isActive }) => (isActive ? 'active' : '')}>
            <Icon name={item.icon} size={22} />
            {item.label}
          </NavLink>
        ))}
        <button type="button" ref={moreButton} className={more ? 'active' : ''} aria-haspopup="dialog"
          aria-expanded={more} aria-controls={more ? 'more-menu' : undefined} onClick={() => setMore(!more)}>
          <Icon name="more" size={22} />
          More
        </button>
      </nav>
    </div>
  );
}
