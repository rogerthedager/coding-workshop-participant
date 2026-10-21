import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

/** Every API route matches on any origin, so VITE_API_URL does not matter. */
export const API = '*/api/v1';

export const apiError = (status, code, message, details) =>
  HttpResponse.json({ error: { code, message, details } }, { status });

export const employee = {
  id: 'u-emp', email: 'employee@acme.inc', full_name: 'Jamie Chen', roles: [],
  must_change_password: false, default_seat_id: null,
};
export const admin = {
  id: 'u-admin', email: 'admin@acme.inc', full_name: 'Alex Admin', roles: ['admin'],
  must_change_password: false, default_seat_id: null,
};

export const session = (user, token = 'token-1') => ({ access_token: token, user });

export const categories = [
  { id: 'c-hvac', name: 'HVAC and temperature', parent_id: null },
  { id: 'c-it', name: 'IT and AV equipment', parent_id: null },
  { id: 'c-proj', name: 'Projector', parent_id: 'c-it' },
];

export const buildings = [
  {
    id: 'b-hq', name: 'Headquarters', code: 'HQ',
    floors: [
      { id: 'f-hq1', name: 'Floor 1', seats: [{ id: 's-101', code: 'HQ-101' }, { id: 's-102', code: 'HQ-102' }] },
      { id: 'f-hq2', name: 'Floor 2', seats: [{ id: 's-201', code: 'HQ-201' }] },
    ],
  },
  {
    id: 'b-an', name: 'Annex', code: 'AN',
    floors: [{ id: 'f-an1', name: 'Annex ground', seats: [{ id: 's-a1', code: 'AN-1' }] }],
  },
];

export const engineer = {
  id: 'u-eng', email: 'engineer@acme.inc', full_name: 'Sam Rivera', roles: ['engineer'],
  must_change_password: false, default_seat_id: null,
};

/** Detail and list responses name the people they reference ({ userId: full_name }). */
export function incident(overrides = {}) {
  return {
    id: 'i-1', number: 'INC-0001', title: 'Projector will not turn on', status: 'Open', priority: 'High',
    sla_state: 'on_track', building_id: 'b-hq', floor_id: 'f-hq1', seat_id: null,
    assigned_to: 'u-eng', created_by: 'u-emp', created_at: '2026-10-20T08:00:00Z',
    updated_at: '2026-10-20T09:00:00.123456Z', permissions: { staff: true, admin: true },
    people: { 'u-emp': employee.full_name, 'u-eng': engineer.full_name },
    ...overrides,
  };
}

export const page = (items, extra = {}) => ({ items, total: items.length, page: 1, page_size: 25, ...extra });

/** Signed in as `user` by default: AuthProvider restores the session with /auth/refresh on load. */
export const signedInAs = (user) => http.post(`${API}/auth/refresh`, () => HttpResponse.json(session(user)));

export const handlers = [
  signedInAs(employee),
  http.get(`${API}/categories`, () => HttpResponse.json({ items: categories })),
  http.get(`${API}/locations/tree`, () => HttpResponse.json({ items: buildings })),
  http.get(`${API}/engineers`, () => HttpResponse.json({ items: [] })),
  http.get(`${API}/users`, () => HttpResponse.json({ items: [] })),
  http.get(`${API}/notifications/unread-count`, () => HttpResponse.json({ unread: 0 })),
  http.get(`${API}/dashboard/employee`, () => HttpResponse.json({})),
  http.get(`${API}/incidents/similar`, () => HttpResponse.json({ items: [] })),
  http.get(`${API}/incidents`, () => HttpResponse.json(page([]))),
];

export const server = setupServer(...handlers);
