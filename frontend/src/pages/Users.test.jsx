import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { admin, API, employee, page, server, signedInAs } from '../test/server';
import { currentUrl, renderApp } from '../test/render';

const me = { ...admin, is_active: true };
const other = { ...employee, is_active: true };

let patches;
let current;
beforeEach(() => {
  patches = [];
  current = me;
  server.use(
    signedInAs(admin),
    http.get(`${API}/users`, () => HttpResponse.json(page([me, other]))),
    http.patch(`${API}/users/:id`, async ({ params, request }) => {
      const body = await request.json();
      patches.push([params.id, body]);
      if (params.id === me.id) current = { ...me, ...body };
      return HttpResponse.json({ ...(params.id === me.id ? me : other), ...body });
    }),
    http.get(`${API}/auth/me`, () => HttpResponse.json(current)),
    http.post(`${API}/auth/logout`, () => new HttpResponse(null, { status: 204 })),
  );
});

async function openUsers() {
  const view = renderApp('/admin/users');
  await screen.findByRole('heading', { name: 'Users' });
  const row = (await screen.findByText(`${me.full_name} (you)`)).closest('li');
  return { ...view, row };
}

describe('editing your own account', () => {
  it('asks before removing your own admin role, then refreshes your roles', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    const { user, row } = await openUsers();
    await user.click(within(row).getByRole('checkbox', { name: 'Admin' }));

    await user.click(within(row).getByRole('button', { name: 'Save' }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/remove your own admin role/i));
    expect(patches).toEqual([]);

    await user.click(within(row).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patches).toEqual([[me.id, { roles: [], is_active: true }]]));
    // /auth/me now says not an admin, so the admin-only page sends you home.
    await waitFor(() => expect(currentUrl().pathname).toBe('/'));
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument();
  });

  it('signs you out after you deactivate your own account', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { user, row } = await openUsers();
    await user.click(within(row).getByRole('checkbox', { name: 'Active' }));
    await user.click(within(row).getByRole('button', { name: 'Save' }));

    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/deactivate your own account/i));
    expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument();
    expect(currentUrl().pathname).toBe('/login');
    expect(patches).toEqual([[me.id, { roles: ['admin'], is_active: false }]]);
  });

  it('does not ask when editing someone else', async () => {
    const confirm = vi.spyOn(window, 'confirm');
    const { user } = await openUsers();
    const row = screen.getByText(other.full_name).closest('li');
    await user.click(within(row).getByRole('checkbox', { name: 'Active' }));
    await user.click(within(row).getByRole('button', { name: 'Save' }));

    expect(await screen.findByText(`${other.full_name} saved.`)).toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
  });
});
