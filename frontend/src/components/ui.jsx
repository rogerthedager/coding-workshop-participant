import { useEffect } from 'react';
import { statusLabel } from '../format';

// Small presentational pieces shared by every page.

const ICONS = {
  list: 'M4 6h16M4 12h16M4 18h10',
  plus: 'M12 5v14M5 12h14',
  search: 'M20 20l-3.5-3.5',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  wrench: 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z',
  filter: 'M3 4h18l-7 8v6l-4 2v-8z',
  team: 'M2 21c0-4 3-6 7-6s7 2 7 6M17 11h6M20 8v6',
  overview: 'M3 3v18h18M7 15l4-4 3 3 5-6',
  back: 'M15 18l-6-6 6-6',
  check: 'M8 12l3 3 5-6',
  up: 'M12 19V5M5 12l7-7 7 7',
  stop: 'M7 7l10 10',
  logout: 'M15 4h4v16h-4M10 17l-5-5 5-5M5 12h11',
  more: 'M5 12h.5M11.75 12h.5M18.5 12h.5',
  sun: 'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  monitor: 'M3 4h18v12H3zM8 20h8M12 16v4',
};

export function Icon({ name, size = 20, stroke = 2 }) {
  const circle = { search: [11, 11, 7], check: [12, 12, 9], stop: [12, 12, 9], team: [9, 8, 4], sun: [12, 12, 4] }[name];
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {circle && <circle cx={circle[0]} cy={circle[1]} r={circle[2]} />}
      <path d={ICONS[name]} />
    </svg>
  );
}

const slug = (value) => String(value || '').toLowerCase().replace(/[^a-z]+/g, '-');

export function StatusChip({ status }) {
  return <span className={`chip s-${slug(status)}`}>{statusLabel(status)}</span>;
}

export function Priority({ value, suffix = '' }) {
  return (
    <span className={`prio p-${slug(value)}`}>
      <i aria-hidden="true" />
      {value}{suffix}
    </span>
  );
}

export function Availability({ value }) {
  const label = value === 'Off-duty' ? 'Off duty' : value;
  return <span className={`chip av-${slug(value)}`}>{label}</span>;
}

export function Loading() {
  return <div className="empty" role="status">Loading…</div>;
}

export function ErrorBox({ error }) {
  if (!error) return null;
  return <div className="alert" role="alert">{error.message || String(error)}</div>;
}

/**
 * Persistent polite live region; the toast clears itself after `ms`.
 * `toast` is { text, id } from useToast(); each new value restarts the timer and is
 * re-rendered (so re-announced) even when the text repeats.
 * `onDone` is called with null and should be a stable state setter.
 */
export function Toast({ toast, onDone, ms = 5000 }) {
  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => onDone(null), ms);
    return () => window.clearTimeout(timer);
  }, [toast, onDone, ms]);
  return (
    <div className="toast-region" aria-live="polite" role="status">
      {toast && <div key={toast.id} className="toast">{toast.text}</div>}
    </div>
  );
}

const SLA_TEXT = {
  on_track: 'On track', at_risk: 'At risk', breached: 'SLA breached', met: 'SLA met', missed: 'SLA missed',
};

/** SLA state as text and color (never color alone). */
export function SlaChip({ state }) {
  if (!state) return null;
  return <span className={`chip sla-${state.replace('_', '-')}`}>{SLA_TEXT[state] || state}</span>;
}

/** Bell icon with an unread badge. */
export function BellIcon({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  );
}
