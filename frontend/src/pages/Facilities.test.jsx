import { screen, within } from '@testing-library/react';
import { delay, http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import { admin, API, apiError, server, signedInAs } from '../test/server';
import { renderApp } from '../test/render';

const tree = [{
  id: 'b-hq', name: 'Headquarters', code: 'HQ', is_active: true,
  floors: [{ id: 'f-hq1', name: 'Floor 1', level_number: 1, is_active: true, seats: [] }],
}];

beforeEach(() => {
  server.use(
    signedInAs(admin),
    http.get(`${API}/locations/tree`, () => HttpResponse.json({ items: tree })),
    http.get(`${API}/category-groups`, () => HttpResponse.json({ items: [] })),
  );
});

async function openFacilities() {
  const view = renderApp('/admin/facilities');
  await screen.findByRole('heading', { name: 'Facilities' });
  return view;
}

describe('facility errors', () => {
  it('names the clashing field of a duplicate building, and sends one request on a double click', async () => {
    let posts = 0;
    server.use(http.post(`${API}/buildings`, async () => {
      posts += 1;
      await delay(50);
      return apiError(409, 'duplicate', 'That code is already in use', { field: 'code' });
    }));
    const { user } = await openFacilities();
    await user.click(screen.getByRole('button', { name: 'Add building' }));
    await user.type(screen.getByLabelText('Name'), 'Annex 2');
    await user.type(screen.getByLabelText('Code'), 'hq');
    await user.dblClick(screen.getByRole('button', { name: 'Add building' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('That code is already in use. Pick a different one.');
    expect(posts).toBe(1);
  });

  it('explains a generic unique-constraint conflict, e.g. a floor name', async () => {
    server.use(http.post(`${API}/buildings/:id/floors`, () =>
      apiError(409, 'conflict', 'A uniqueness or reference constraint was violated')));
    const { user } = await openFacilities();
    await user.click(screen.getByRole('button', { name: 'Add floor' }));
    await user.type(screen.getByLabelText('Floor name'), 'Floor 1');
    await user.type(screen.getByLabelText('Level number'), '1');
    await user.click(within(screen.getByRole('heading', { name: /Add floor to/ }).closest('form'))
      .getByRole('button', { name: 'Add floor' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('That name or code is already in use.');
  });

  it('says how many open tickets block a deactivation', async () => {
    server.use(http.patch(`${API}/buildings/:id`, () =>
      apiError(409, 'has_open_incidents', 'Location has non-final incidents', { count: 3 })));
    const { user } = await openFacilities();
    await user.click(screen.getByRole('button', { name: 'Deactivate' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('3 open tickets still use this location.');
  });
});
