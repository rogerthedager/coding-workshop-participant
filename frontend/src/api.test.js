import { File as NodeFile } from 'node:buffer';
import { delay, http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { API, apiError, employee, server, session } from './test/server';

// api.js keeps the access token in module state, so each test gets a fresh copy.
let mod;
beforeEach(async () => {
  vi.resetModules();
  mod = await import('./api');
});

/** Refresh issues token-1, token-2, …; returns the call counter. */
function rotatingRefresh({ wait = 0 } = {}) {
  const calls = { count: 0 };
  server.use(
    http.post(`${API}/auth/refresh`, async () => {
      calls.count += 1;
      const token = `token-${calls.count}`;
      if (wait) await delay(wait);
      return HttpResponse.json(session(employee, token));
    }),
  );
  return calls;
}

/** A protected route that accepts only `token` and reports any other as expired. */
function protectedRoute(path, token, body = { ok: true }) {
  const seen = [];
  server.use(
    http.get(`${API}${path}`, ({ request }) => {
      const auth = request.headers.get('Authorization');
      seen.push(auth);
      if (auth !== `Bearer ${token}`) return apiError(401, 'token_expired', 'Access token expired.');
      return HttpResponse.json(body);
    }),
  );
  return seen;
}

describe('request refresh-and-retry', () => {
  it('refreshes once and retries when the access token has expired', async () => {
    const refresh = rotatingRefresh();
    await mod.refreshSession(); // token-1
    const seen = protectedRoute('/incidents/i-1', 'token-2', { id: 'i-1' });

    await expect(mod.api.incident('i-1')).resolves.toEqual({ id: 'i-1' });
    expect(refresh.count).toBe(2);
    expect(seen).toEqual(['Bearer token-1', 'Bearer token-2']);
  });

  it('also refreshes on a generic unauthenticated 401 outside /auth/*', async () => {
    const refresh = rotatingRefresh();
    await mod.refreshSession();
    server.use(
      http.get(`${API}/incidents/i-1`, ({ request }) =>
        request.headers.get('Authorization') === 'Bearer token-2'
          ? HttpResponse.json({ id: 'i-1' })
          : apiError(401, 'unauthenticated', 'Not signed in.')),
    );
    await expect(mod.api.incident('i-1')).resolves.toEqual({ id: 'i-1' });
    expect(refresh.count).toBe(2);
  });

  it('retries only once when the retried request fails too', async () => {
    const refresh = rotatingRefresh();
    await mod.refreshSession();
    const seen = protectedRoute('/incidents/i-1', 'never-valid');

    await expect(mod.api.incident('i-1')).rejects.toMatchObject({ status: 401, code: 'token_expired' });
    expect(seen).toHaveLength(2);
    expect(refresh.count).toBe(2);
  });

  it('does not refresh on a 403', async () => {
    const refresh = rotatingRefresh();
    await mod.refreshSession();
    server.use(http.get(`${API}/incidents/i-1`, () => apiError(403, 'forbidden', 'Not allowed.')));
    await expect(mod.api.incident('i-1')).rejects.toMatchObject({ status: 403 });
    expect(refresh.count).toBe(1);
  });

  it('does not refresh on a 401 from /auth/change-password', async () => {
    const refresh = rotatingRefresh();
    await mod.refreshSession();
    let posts = 0;
    server.use(
      http.post(`${API}/auth/change-password`, () => {
        posts += 1;
        return apiError(401, 'unauthenticated', 'Not signed in.');
      }),
    );

    await expect(mod.api.changePassword({ current_password: 'a', new_password: 'b' }))
      .rejects.toMatchObject({ status: 401, code: 'unauthenticated' });
    expect(posts).toBe(1);
    expect(refresh.count).toBe(1);
  });

  it('keeps the session when the current password is wrong (422)', async () => {
    const listener = vi.fn();
    mod.setSessionListener(listener);
    const refresh = rotatingRefresh();
    await mod.refreshSession();
    listener.mockClear();
    server.use(
      http.post(`${API}/auth/change-password`, () =>
        apiError(422, 'invalid_current_password', 'Current password is incorrect.')),
    );

    await expect(mod.api.changePassword({ current_password: 'wrong', new_password: 'abcdefghij1' }))
      .rejects.toMatchObject({ status: 422, code: 'invalid_current_password' });
    expect(refresh.count).toBe(1);
    expect(listener).not.toHaveBeenCalled();
  });

  it('does not refresh a 401 from login (bad credentials)', async () => {
    const refresh = rotatingRefresh();
    server.use(http.post(`${API}/auth/login`, () => apiError(401, 'invalid_credentials', 'Wrong email or password.')));

    await expect(mod.api.login('a@acme.inc', 'x')).rejects.toMatchObject({ status: 401, message: 'Wrong email or password.' });
    expect(refresh.count).toBe(0);
  });

  it('shares one refresh between requests that expire at the same time', async () => {
    const refresh = rotatingRefresh({ wait: 30 });
    await mod.refreshSession(); // token-1
    const seenA = protectedRoute('/incidents/a', 'token-2', { id: 'a' });
    const seenB = protectedRoute('/incidents/b', 'token-2', { id: 'b' });

    const results = await Promise.all([mod.api.incident('a'), mod.api.incident('b')]);
    expect(results).toEqual([{ id: 'a' }, { id: 'b' }]);
    expect(refresh.count).toBe(2); // the initial sign-in plus exactly one shared refresh
    expect(seenA).toEqual(['Bearer token-1', 'Bearer token-2']);
    expect(seenB).toEqual(['Bearer token-1', 'Bearer token-2']);
  });

  it('signs out when the refresh itself fails', async () => {
    const listener = vi.fn();
    mod.setSessionListener(listener);
    server.use(http.post(`${API}/auth/refresh`, () => apiError(401, 'unauthenticated', 'No session.')));
    await expect(mod.refreshSession()).rejects.toMatchObject({ status: 401 });
    expect(listener).toHaveBeenLastCalledWith(null);
  });
});

describe('ApiError', () => {
  it('reads the error envelope and the first field detail', () => {
    const error = new mod.ApiError(422, {
      error: { code: 'validation_error', message: 'Invalid input.', details: [{ field: 'title', message: 'Too short.' }] },
    });
    expect(error).toMatchObject({ status: 422, code: 'validation_error', message: 'Invalid input.' });
    expect(error.detail).toBe('Title: Too short.');
    expect(new mod.ApiError(500, null).message).toBe('Request failed (500)');
  });

  it.each([
    [{ location: ['body', 'reason'], message: 'String should have at least 1 character', type: 'string_too_short' }, 'Reason: required'],
    [{ location: ['body', 'reason'], message: 'String should have at least 10 characters', type: 'string_too_short' }, 'Reason: use at least 10 characters'],
    [{ location: ['body', 'decision_reason'], message: 'String should have at most 500 characters', type: 'string_too_long' }, 'Decision reason: use at most 500 characters'],
    [{ location: ['body', 'priority'], message: "Input should be 'Low' or 'High'", type: 'enum' }, "Priority: must be 'Low' or 'High'"],
    [{ location: ['body'], message: 'Field required', type: 'missing' }, 'Required'],
  ])('rewords the Pydantic detail %o', (detail, expected) => {
    const error = new mod.ApiError(422, { error: { code: 'validation_error', message: 'Invalid request', details: [detail] } });
    expect(error.detail).toBe(expected);
  });
});

describe('optimistic concurrency', () => {
  const ticket = { id: 'i-1', updated_at: '2026-10-20T09:00:00.123456Z' };

  it.each([
    ['transition', 'post', '/incidents/i-1/transitions', (api) => api.transition(ticket, { status: 'In Progress' })],
    ['updateIncident', 'patch', '/incidents/i-1', (api) => api.updateIncident(ticket, { title: 'New title' })],
    ['assign', 'post', '/incidents/i-1/assign', (api) => api.assign(ticket, { assigned_to: 'u-eng' })],
    ['setPriority', 'patch', '/incidents/i-1/priority', (api) => api.setPriority(ticket, { priority: 'Low' })],
    ['addNote', 'post', '/incidents/i-1/notes', (api) => api.addNote(ticket, { body: 'x', visibility: 'public' })],
    ['escalate', 'post', '/incidents/i-1/escalation', (api) => api.escalate(ticket, 'Urgent')],
  ])('%s sends If-Match with the version the user saw', async (_name, method, path, call) => {
    let ifMatch;
    server.use(
      http[method](`${API}${path}`, ({ request }) => {
        ifMatch = request.headers.get('If-Match');
        return HttpResponse.json({ id: 'i-1' });
      }),
    );
    await call(mod.api);
    expect(ifMatch).toBe(ticket.updated_at);
  });

  it('does not send If-Match on reads', async () => {
    let ifMatch = 'unset';
    server.use(http.get(`${API}/incidents/i-1`, ({ request }) => {
      ifMatch = request.headers.get('If-Match');
      return HttpResponse.json({ id: 'i-1' });
    }));
    await mod.api.incident('i-1');
    expect(ifMatch).toBeNull();
  });
});

describe('query strings', () => {
  it('repeats array values and drops empty ones', async () => {
    let url;
    server.use(http.get(`${API}/incidents`, ({ request }) => {
      url = new URL(request.url);
      return HttpResponse.json({ items: [] });
    }));
    await mod.api.incidents({ status: ['Open', 'Blocked'], priority: '', q: null, page: 2 });
    expect(url.searchParams.getAll('status')).toEqual(['Open', 'Blocked']);
    expect(url.searchParams.has('priority')).toBe(false);
    expect(url.searchParams.has('q')).toBe(false);
    expect(url.searchParams.get('page')).toBe('2');
  });
});

describe('attachment upload', () => {
  const BUCKET = 'https://bucket.example.test/upload';
  // Node's fetch (which MSW intercepts) cannot serialize jsdom's FormData/File; browsers have one
  // implementation, so give api.js Node's for these tests.
  const File = NodeFile;
  beforeEach(async () => {
    const NodeFormData = (await new Response(new URLSearchParams('a=1')).formData()).constructor;
    vi.stubGlobal('FormData', NodeFormData);
    return () => vi.unstubAllGlobals();
  });

  function uploadFlow({ bucketStatus = 204 } = {}) {
    const log = [];
    server.use(
      http.post(`${API}/incidents/i-1/attachments/presign`, async ({ request }) => {
        log.push(['presign', await request.json()]);
        return HttpResponse.json({
          attachment_id: 'att-1',
          upload: { url: BUCKET, fields: { key: 'incidents/i-1/att-1', policy: 'signed-policy' } },
        });
      }),
      http.post(BUCKET, async ({ request }) => {
        // Parse the multipart body by hand: undici's parser rejects files under vitest's jsdom globals.
        const raw = await request.text();
        const parts = [...raw.matchAll(/name="([^"]+)"(?:; filename="([^"]+)")?[^\r]*\r\n(?:[^\r]+\r\n)*\r\n([^\r]*)\r\n/g)]
          .map(([, name, filename, value]) => ({ name, filename, value }));
        log.push(['bucket', {
          contentType: request.headers.get('Content-Type')?.split(';')[0],
          auth: request.headers.get('Authorization'),
          parts,
        }]);
        return new HttpResponse(null, { status: bucketStatus });
      }),
      http.post(`${API}/incidents/i-1/attachments`, async ({ request }) => {
        log.push(['finalize', await request.json()]);
        return HttpResponse.json({ id: 'att-1', status: 'ready' }, { status: 201 });
      }),
    );
    return log;
  }

  it('presigns, posts the file to the presigned URL, then finalizes', async () => {
    rotatingRefresh();
    await mod.refreshSession();
    const log = uploadFlow();
    const file = new File(['png-bytes'], 'leak.png', { type: 'image/png' });

    await expect(mod.api.uploadAttachment('i-1', file, 'n-1')).resolves.toEqual({ id: 'att-1', status: 'ready' });
    expect(log.map(([step]) => step)).toEqual(['presign', 'bucket', 'finalize']);
    expect(log[0][1]).toEqual({ content_type: 'image/png', size_bytes: file.size, note_id: 'n-1' });
    // S3 requires the policy fields before the file, and the bucket never sees our bearer token.
    expect(log[1][1]).toEqual({
      contentType: 'multipart/form-data',
      auth: null,
      parts: [
        { name: 'key', filename: undefined, value: 'incidents/i-1/att-1' },
        { name: 'policy', filename: undefined, value: 'signed-policy' },
        { name: 'file', filename: 'leak.png', value: 'png-bytes' },
      ],
    });
    expect(log[2][1]).toEqual({ attachment_id: 'att-1' });
  });

  it('infers the type from the extension when the browser gives none', async () => {
    const log = uploadFlow();
    await mod.api.uploadAttachment('i-1', new File(['x'], 'photo.HEIC', { type: '' }));
    expect(log[0][1].content_type).toBe('image/heic');
  });

  it('rejects unknown file types before calling the API', async () => {
    const log = uploadFlow();
    await expect(mod.api.uploadAttachment('i-1', new File(['%PDF'], 'scan.pdf', { type: 'application/pdf' })))
      .rejects.toMatchObject({ status: 422, message: expect.stringContaining('scan.pdf') });
    await expect(mod.api.uploadAttachment('i-1', new File(['x'], 'notes.txt', { type: '' })))
      .rejects.toMatchObject({ status: 422 });
    expect(log).toEqual([]);
  });

  it('retries finalize once when the object is not in the bucket yet', async () => {
    uploadFlow();
    const finalizes = [];
    server.use(http.post(`${API}/incidents/i-1/attachments`, () => {
      finalizes.push(Date.now());
      return finalizes.length === 1
        ? apiError(409, 'upload_incomplete', 'The upload has not arrived yet.')
        : HttpResponse.json({ id: 'att-1', status: 'ready' }, { status: 201 });
    }));

    await expect(mod.api.uploadAttachment('i-1', new File(['x'], 'a.jpg', { type: 'image/jpeg' })))
      .resolves.toEqual({ id: 'att-1', status: 'ready' });
    expect(finalizes).toHaveLength(2);
    expect(finalizes[1] - finalizes[0]).toBeGreaterThanOrEqual(900);
  });

  it('does not retry finalize for other errors', async () => {
    uploadFlow();
    let finalizes = 0;
    server.use(http.post(`${API}/incidents/i-1/attachments`, () => {
      finalizes += 1;
      return apiError(409, 'attachment_limit', 'A ticket can have at most 5 photos.');
    }));
    await expect(mod.api.uploadAttachment('i-1', new File(['x'], 'a.jpg', { type: 'image/jpeg' })))
      .rejects.toMatchObject({ status: 409, code: 'attachment_limit' });
    expect(finalizes).toBe(1);
  });

  it('does not finalize when the bucket refuses the upload', async () => {
    const log = uploadFlow({ bucketStatus: 403 });
    await expect(mod.api.uploadAttachment('i-1', new File(['x'], 'a.jpg', { type: 'image/jpeg' })))
      .rejects.toMatchObject({ status: 403, message: expect.stringMatching(/refused/) });
    expect(log.map(([step]) => step)).toEqual(['presign', 'bucket']);
  });
});

describe('imageType', () => {
  it.each([
    [{ name: 'a.png', type: 'image/png' }, 'image/png'],
    [{ name: 'a.gif', type: 'image/gif' }, null],
    [{ name: 'a.JPG', type: '' }, 'image/jpeg'],
    [{ name: 'a.webp', type: '' }, 'image/webp'],
    [{ name: 'archive', type: '' }, null],
  ])('%o → %s', (file, expected) => {
    expect(mod.imageType(file)).toBe(expected);
  });
});

describe('appHref', () => {
  it('maps API list links to app routes', () => {
    expect(mod.appHref('https://api.example/api/v1/incidents?status=Blocked')).toBe('/incidents?status=Blocked');
    expect(mod.appHref(null)).toBe('/incidents');
  });
});

describe('engineer dashboard', () => {
  it('sends the viewer time zone so "this week" starts on their local Monday', async () => {
    let tz;
    server.use(http.get(`${API}/dashboard/engineer`, ({ request }) => {
      tz = new URL(request.url).searchParams.get('tz');
      return HttpResponse.json({});
    }));
    await mod.api.engineerDashboard();
    expect(tz).toBeTruthy();
    expect(tz).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });
});

describe('notification reads', () => {
  it('fires a window event after marking one or all read', async () => {
    server.use(
      http.post(`${API}/notifications/:id/read`, () => new HttpResponse(null, { status: 204 })),
      http.post(`${API}/notifications/read-all`, () => new HttpResponse(null, { status: 204 })),
    );
    const seen = vi.fn();
    window.addEventListener(mod.NOTIFICATIONS_CHANGED, seen);
    try {
      await mod.api.readNotification('n-1');
      await mod.api.readAllNotifications();
    } finally {
      window.removeEventListener(mod.NOTIFICATIONS_CHANGED, seen);
    }
    expect(seen).toHaveBeenCalledTimes(2);
  });
});
