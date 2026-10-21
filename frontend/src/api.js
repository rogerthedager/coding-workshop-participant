// Thin client for the facility-service API.
// The access token lives only in memory; the refresh token is an HttpOnly cookie.

const BASE = `${(import.meta.env.VITE_API_URL || '').replace(/\/$/, '')}/api/v1`;

let accessToken = null;
let refreshing = null;
let onSessionChange = () => {};

export class ApiError extends Error {
  constructor(status, body) {
    const error = body?.error || {};
    super(error.message || `Request failed (${status})`);
    this.status = status;
    this.code = error.code;
    this.details = error.details;
  }

  /** First field-level message from a 422, else the general message. */
  get detail() {
    if (Array.isArray(this.details) && this.details.length) return readableDetail(this.details[0]);
    return this.message;
  }
}

/** One 422 field error as "<Field>: <message>", rewording Pydantic's text for people. */
function readableDetail({ location, field, message = '', type }) {
  const skip = ['body', 'query', 'path', 'header'];
  const name = field || [].concat(location || []).filter((part) => typeof part === 'string' && !skip.includes(part)).at(-1);
  const count = /\d+/.exec(message)?.[0];
  const text = {
    missing: 'required',
    string_too_short: count === '1' ? 'required' : `use at least ${count} characters`,
    string_too_long: `use at most ${count} characters`,
    extra_forbidden: 'not accepted here',
    uuid_parsing: 'not a valid ID',
  }[type] || message.replace(/^Value error, /, '').replace(/^Input should be/, 'must be');
  if (!name) return text.charAt(0).toUpperCase() + text.slice(1);
  const label = name.replace(/_/g, ' ');
  return `${label.charAt(0).toUpperCase()}${label.slice(1)}: ${text}`;
}

/** Window event fired after notifications are marked read, so the bell re-checks its count. */
export const NOTIFICATIONS_CHANGED = 'notifications:changed';
const notificationsChanged = (data) => {
  window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED));
  return data;
};

// The viewer's IANA time zone, so "this week" starts on their local Monday.
const localTz = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
// Finalizing can race the bucket's write; retry once after this delay.
const FINALIZE_RETRY_MS = 1000;

export function setSessionListener(listener) {
  onSessionChange = listener;
}

function applySession(session) {
  accessToken = session?.access_token || null;
  onSessionChange(session?.user || null);
  return session;
}

async function send(path, { method = 'GET', body, query, version, auth = true } = {}) {
  const url = new URL(BASE + path, window.location.origin);
  for (const [key, value] of Object.entries(query || {})) {
    if (value === undefined || value === null || value === '') continue;
    for (const item of [].concat(value)) url.searchParams.append(key, item);
  }
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (version) headers['If-Match'] = version;
  const response = await fetch(url, {
    method,
    headers,
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

/** One refresh at a time: replaying a rotated cookie revokes the whole session. */
export function refreshSession() {
  refreshing ||= send('/auth/refresh', { method: 'POST', auth: false })
    .then(applySession)
    .catch((error) => {
      applySession(null);
      throw error;
    })
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

/** Refresh and retry once, but only when the access token itself has expired. */
function shouldRefresh(path, error) {
  if (error.status !== 401 || !accessToken) return false;
  if (error.code === 'token_expired') return true;
  // Other 401s on /auth/* mean bad credentials, not a stale token.
  return error.code === 'unauthenticated' && !path.startsWith('/auth/');
}

export async function request(path, options = {}) {
  try {
    return await send(path, options);
  } catch (error) {
    if (options.auth === false || !shouldRefresh(path, error)) throw error;
    await refreshSession();
    return send(path, options);
  }
}

/** Download an authenticated file (e.g. CSV export) via a temporary object URL. */
export async function download(path, { query, filename } = {}) {
  const fetchFile = () => {
    const url = new URL(BASE + path, window.location.origin);
    for (const [key, value] of Object.entries(query || {})) {
      if (value === undefined || value === null || value === '') continue;
      for (const item of [].concat(value)) url.searchParams.append(key, item);
    }
    return fetch(url, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      credentials: 'include',
    });
  };
  let response = await fetchFile();
  if (response.status === 401 && accessToken) {
    const body = await response.clone().json().catch(() => null);
    if (shouldRefresh(path, new ApiError(401, body))) {
      await refreshSession();
      response = await fetchFile();
    }
  }
  if (!response.ok) throw new ApiError(response.status, await response.json().catch(() => null));
  const disposition = response.headers.get('Content-Disposition') || '';
  const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)?.[1] || filename || 'download';
  const href = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = href;
  link.download = decodeURIComponent(name);
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

// Media types the backend accepts (AttachmentType in facility/enums.py).
export const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/heic'];
const EXTENSION_TYPES = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', heic: 'image/heic' };

/** The accepted media type for a file, inferred from its extension when the browser gives none; else null. */
export function imageType(file) {
  if (file.type) return IMAGE_TYPES.includes(file.type) ? file.type : null;
  return EXTENSION_TYPES[file.name.split('.').pop().toLowerCase()] || null;
}

export const api = {
  login: (email, password) =>
    send('/auth/login', { method: 'POST', body: { email, password }, auth: false }).then(applySession),
  register: (body) => send('/auth/register', { method: 'POST', body, auth: false }),
  logout: () => send('/auth/logout', { method: 'POST' }).finally(() => applySession(null)),
  changePassword: (body) => request('/auth/change-password', { method: 'POST', body }).then(applySession),

  incidents: (query) => request('/incidents', { query }),
  exportIncidents: (query) => download('/incidents/export.csv', { query, filename: 'incidents.csv' }),
  incident: (id) => request(`/incidents/${id}`),
  createIncident: (body) => request('/incidents', { method: 'POST', body }),
  updateIncident: (incident, body) =>
    request(`/incidents/${incident.id}`, { method: 'PATCH', body, version: incident.updated_at }),
  assign: (incident, body) =>
    request(`/incidents/${incident.id}/assign`, { method: 'POST', body, version: incident.updated_at }),
  setPriority: (incident, body) =>
    request(`/incidents/${incident.id}/priority`, { method: 'PATCH', body, version: incident.updated_at }),
  transition: (incident, body) =>
    request(`/incidents/${incident.id}/transitions`, { method: 'POST', body, version: incident.updated_at }),
  escalate: (incident, reason) =>
    request(`/incidents/${incident.id}/escalation`, {
      method: 'POST', body: { reason }, version: incident.updated_at,
    }),
  decideEscalation: (incident, body) =>
    request(`/incidents/${incident.id}/escalation/decision`, {
      method: 'POST', body, version: incident.updated_at,
    }),
  notes: (id) => request(`/incidents/${id}/notes`),
  addNote: (incident, body) =>
    request(`/incidents/${incident.id}/notes`, { method: 'POST', body, version: incident.updated_at }),
  hideNote: (note, reason) =>
    request(`/notes/${note.id}/hide`, { method: 'POST', body: { reason }, version: note.updated_at }),
  timeline: (id) => request(`/incidents/${id}/timeline`),

  employeeDashboard: () => request('/dashboard/employee'),
  engineerDashboard: () => request('/dashboard/engineer', { query: { tz: localTz() } }),
  adminDashboard: (query) => request('/dashboard/admin', { query }),

  categories: () => request('/categories', { query: { active_only: true } }),
  locations: () => request('/locations/tree'),
  engineers: (query) => request('/engineers', { query: { page_size: 100, ...query } }),
  createEngineer: (body) => request('/engineers', { method: 'POST', body }),
  updateEngineer: (id, body) => request(`/engineers/${id}`, { method: 'PATCH', body }),
  setMyAvailability: (availability) =>
    request('/engineers/me/availability', { method: 'PATCH', body: { availability } }),
  users: (query) => request('/users', { query: { page_size: 100, ...query } }),
  me: () => request('/auth/me'),
  updateMe: (body) => request('/users/me', { method: 'PATCH', body }),
  updateUser: (id, body) => request(`/users/${id}`, { method: 'PATCH', body }),

  // Routing, following and duplicates
  similar: (categoryId, floorId) =>
    request('/incidents/similar', { query: { category_id: categoryId, floor_id: floorId } }),
  follow: (id) => request(`/incidents/${id}/follow`, { method: 'POST' }),
  unfollow: (id) => request(`/incidents/${id}/follow`, { method: 'DELETE' }),
  markDuplicate: (incident, body) =>
    request(`/incidents/${incident.id}/duplicate`, { method: 'POST', body, version: incident.updated_at }),
  returnToTriage: (incident, reason) =>
    request(`/incidents/${incident.id}/return-to-triage`, {
      method: 'POST', body: { reason }, version: incident.updated_at,
    }),
  suggestions: (id) => request(`/incidents/${id}/engineer-suggestions`),
  editNote: (note, body) =>
    request(`/notes/${note.id}`, { method: 'PATCH', body: { body }, version: note.updated_at }),

  // Attachments: presign, upload straight to the bucket, then finalize.
  attachments: (id) => request(`/incidents/${id}/attachments`),
  attachmentUrl: (attachmentId) => request(`/attachments/${attachmentId}`),
  async uploadAttachment(incidentId, file, noteId) {
    const contentType = imageType(file);
    if (!contentType) {
      throw new ApiError(422, { error: { message: `${file.name}: use a JPEG, PNG, WebP or HEIC image.` } });
    }
    const presigned = await request(`/incidents/${incidentId}/attachments/presign`, {
      method: 'POST',
      body: { content_type: contentType, size_bytes: file.size, note_id: noteId || undefined },
    });
    const form = new FormData();
    for (const [key, value] of Object.entries(presigned.upload.fields || {})) form.append(key, value);
    form.append('file', file);
    const uploaded = await fetch(presigned.upload.url, { method: 'POST', body: form });
    if (!uploaded.ok) throw new ApiError(uploaded.status, { error: { message: 'The image upload was refused. Check its size and type.' } });
    const finalize = () => request(`/incidents/${incidentId}/attachments`, {
      method: 'POST', body: { attachment_id: presigned.attachment_id },
    });
    try {
      return await finalize();
    } catch (error) {
      // The bucket may not show the object yet; give it a moment once.
      if (error.code !== 'upload_incomplete') throw error;
      await new Promise((resolve) => setTimeout(resolve, FINALIZE_RETRY_MS));
      return finalize();
    }
  },

  // Notifications
  notifications: (query) => request('/notifications', { query }),
  unreadCount: () => request('/notifications/unread-count'),
  readNotification: (id) => request(`/notifications/${id}/read`, { method: 'POST' }).then(notificationsChanged),
  readAllNotifications: () => request('/notifications/read-all', { method: 'POST' }).then(notificationsChanged),

  // Teams and routing groups
  teams: () => request('/teams'),
  team: (id) => request(`/teams/${id}`),
  createTeam: (body) => request('/teams', { method: 'POST', body }),
  updateTeam: (id, body) => request(`/teams/${id}`, { method: 'PATCH', body }),
  setTeamMembers: (id, engineerUserIds) =>
    request(`/teams/${id}/members`, { method: 'PUT', body: { engineer_user_ids: engineerUserIds } }),
  categoryGroups: () => request('/category-groups'),
  createCategoryGroup: (body) => request('/category-groups', { method: 'POST', body }),
  updateCategoryGroup: (id, body) => request(`/category-groups/${id}`, { method: 'PATCH', body }),

  // Facilities and categories (admin)
  allCategories: () => request('/categories'),
  createCategory: (body) => request('/categories', { method: 'POST', body }),
  updateCategory: (id, body) => request(`/categories/${id}`, { method: 'PATCH', body }),
  locationTree: () => request('/locations/tree', { query: { active_only: false } }),
  createBuilding: (body) => request('/buildings', { method: 'POST', body }),
  updateBuilding: (id, body) => request(`/buildings/${id}`, { method: 'PATCH', body }),
  createFloor: (buildingId, body) => request(`/buildings/${buildingId}/floors`, { method: 'POST', body }),
  updateFloor: (id, body) => request(`/floors/${id}`, { method: 'PATCH', body }),
  createSeat: (floorId, body) => request(`/floors/${floorId}/seats`, { method: 'POST', body }),
  bulkSeats: (floorId, body) => request(`/floors/${floorId}/seats/bulk`, { method: 'POST', body }),
  updateSeat: (id, body) => request(`/seats/${id}`, { method: 'PATCH', body }),

  teamDashboard: (query) =>
    request('/dashboard/team', {
      query: { tz: localTz(), ...query },
    }),
};

/** Dashboard hrefs point at API list URLs; map them to the matching app route. */
export function appHref(apiHref) {
  if (!apiHref) return '/incidents';
  return apiHref.replace(/^.*\/api\/v1/, '');
}
