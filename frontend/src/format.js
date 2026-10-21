// Plain formatting helpers shared by pages.

const STATUS_LABEL = { 'In Progress': 'In progress' };
export const statusLabel = (status) => STATUS_LABEL[status] || status;

export function initials(name = '') {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0].toUpperCase()).join('') || '?';
}

export function timeAgo(value) {
  if (!value) return '';
  const seconds = (Date.now() - new Date(value).getTime()) / 1000;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 172800) return 'Yesterday';
  return new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function age(value) {
  if (!value) return '';
  const minutes = Math.max(0, (Date.now() - new Date(value).getTime()) / 60000);
  if (minutes < 60) return `${Math.round(minutes)} min`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} d`;
}

export function dateTime(value) {
  if (!value) return '';
  return new Date(value).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

export function duration(seconds) {
  if (seconds == null) return '–';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

export function roleLabel(user) {
  const roles = user?.roles || [];
  const labels = [
    roles.includes('admin') && 'Admin',
    roles.includes('lead') && 'Team lead',
    roles.includes('engineer') && 'Engineer',
  ].filter(Boolean);
  return labels.length ? labels.join(' · ') : 'Employee';
}

const SLA_LABEL = {
  on_track: 'On track', at_risk: 'At risk', breached: 'SLA breached', met: 'SLA met', missed: 'SLA missed',
};
export const slaLabel = (state) => SLA_LABEL[state] || '';

/** "in 3 h" / "5 h ago" relative to now. */
export function relative(value) {
  if (!value) return '';
  const minutes = (new Date(value).getTime() - Date.now()) / 60000;
  const span = Math.abs(minutes);
  const text = span < 60 ? `${Math.round(span)} min` : span < 1440 ? `${Math.round(span / 60)} h` : `${Math.round(span / 1440)} d`;
  return minutes >= 0 ? `in ${text}` : `${text} ago`;
}

export const pct = (ratio) => (ratio == null ? '–' : `${Math.round(ratio * 100)}%`);
