import { api } from './api';

export const RANK = { Critical: 4, High: 3, Medium: 2, Low: 1 };

/** Highest priority first, then oldest. */
export const byUrgency = (a, b) =>
  RANK[b.priority] - RANK[a.priority] || new Date(a.created_at) - new Date(b.created_at);

/** Assign, asking for confirmation when the backend reports an Off-duty engineer. */
export async function assignWithConfirm(incident, userId) {
  try {
    return await api.assign(incident, { assigned_to: userId });
  } catch (error) {
    if (!error.details?.confirmation_required) throw error;
    if (!window.confirm('This engineer is Off-duty. Assign anyway? They will see it next shift.')) return null;
    return api.assign(incident, { assigned_to: userId, confirm_unavailable: true });
  }
}

/**
 * Why `value` breaks the API's length limits, or '' when it fits. Trimmed and counted
 * in characters, as the API does (str_strip_whitespace, Python len).
 */
export function lengthProblem(value, [min, max]) {
  const n = [...(value || '').trim()].length;
  if (n < min) return min === 1 ? 'Required' : `At least ${min} characters (${n}/${min})`;
  if (n > max) return `At most ${max} characters (${n}/${max})`;
  return '';
}

export function engineerLabel(engineer) {
  return `${engineer.user.full_name} · ${engineer.availability.toLowerCase()} · ${engineer.workload} open`;
}

/** Admin dashboard cohort: the last `days` days, optionally one building. */
export function cohortQuery(days, buildingId) {
  const to = new Date();
  const from = new Date(to.getTime() - Number(days) * 86400000);
  return { from: from.toISOString(), to: to.toISOString(), building_id: buildingId || undefined };
}
