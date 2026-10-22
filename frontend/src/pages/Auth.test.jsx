import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { axe } from 'vitest-axe';
import { API, apiError, employee, server, session, signedInAs } from '../test/server';
import { currentUrl, renderApp } from '../test/render';

const mustChange = { ...employee, must_change_password: true };

/** Count calls to an endpoint without changing its response. */
function countRefreshes(user) {
  const calls = { count: 0 };
  server.use(http.post(`${API}/auth/refresh`, () => {
    calls.count += 1;
    return HttpResponse.json(session(user));
  }));
  return calls;
}

describe('session restore', () => {
  it('sends a signed-out visitor to the login page', async () => {
    server.use(http.post(`${API}/auth/refresh`, () => apiError(401, 'unauthenticated', 'No session.')));
    renderApp('/incidents');
    expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument();
    expect(currentUrl().pathname).toBe('/login');
  });

  it('restores the session from the refresh cookie on load', async () => {
    renderApp('/');
    expect(await screen.findByText(employee.full_name)).toBeInTheDocument();
    expect(currentUrl().pathname).toBe('/');
  });

  it('forces a user with a temporary password to the change-password screen', async () => {
    server.use(signedInAs(mustChange));
    renderApp('/incidents');
    expect(await screen.findByRole('heading', { name: 'Change password' })).toBeInTheDocument();
    expect(currentUrl().pathname).toBe('/change-password');
    expect(screen.getByText(/temporary password/i)).toBeInTheDocument();
  });
});

describe('sign-in redirect', () => {
  it('returns to the page the visitor was heading to, with its query string', async () => {
    let signedIn = false;
    server.use(
      http.post(`${API}/auth/refresh`, () => (signedIn
        ? HttpResponse.json(session(employee))
        : apiError(401, 'unauthenticated', 'No session.'))),
      http.post(`${API}/auth/login`, () => {
        signedIn = true;
        return HttpResponse.json(session(employee));
      }),
    );
    const { user } = renderApp('/incidents?status=Open');
    await screen.findByRole('button', { name: /sign in/i });
    expect(currentUrl().pathname).toBe('/login');

    await user.type(screen.getByLabelText('Work email'), employee.email);
    await user.type(screen.getByLabelText(/^Password/), 'secret-pass-1');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => expect(currentUrl().pathname).toBe('/incidents'));
    expect(currentUrl().search).toBe('?status=Open');
    expect(await screen.findByRole('heading', { name: 'Search' })).toBeInTheDocument();
  });

  it('continues to that page after a forced password change', async () => {
    server.use(signedInAs(mustChange), http.post(`${API}/auth/change-password`, () => HttpResponse.json(session(employee, 'token-2'))));
    const { user } = renderApp('/incidents?status=Blocked');
    await screen.findByRole('heading', { name: 'Change password' });

    await user.type(screen.getByLabelText('Current password'), 'temporary');
    await user.type(screen.getByLabelText('New password'), 'BrandNewPass1');
    await user.click(screen.getByRole('button', { name: 'Save password' }));

    await waitFor(() => expect(currentUrl().pathname).toBe('/incidents'));
    expect(currentUrl().search).toBe('?status=Blocked');
  });
});

describe('forced change password', () => {
  it('shows the error and keeps the user signed in when the current password is wrong', async () => {
    const refresh = countRefreshes(mustChange);
    let posts = 0;
    server.use(http.post(`${API}/auth/change-password`, () => {
      posts += 1;
      return apiError(422, 'invalid_current_password', 'Current password is incorrect.');
    }));
    const { user } = renderApp('/change-password');

    await user.type(await screen.findByLabelText('Current password'), 'not-my-password');
    await user.type(screen.getByLabelText('New password'), 'BrandNewPass1');
    await user.click(screen.getByRole('button', { name: 'Save password' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Your current password is incorrect.');
    expect(posts).toBe(1);
    expect(refresh.count).toBe(1); // no refresh-and-retry, no sign-out
    expect(currentUrl().pathname).toBe('/change-password');
    expect(screen.getByText(`Signed in as ${mustChange.email}`)).toBeInTheDocument();
    expect(screen.getByLabelText('Current password')).toHaveValue('not-my-password');
  });

  it('asks for a valid new password before calling the API', async () => {
    let posts = 0;
    server.use(signedInAs(mustChange), http.post(`${API}/auth/change-password`, () => {
      posts += 1;
      return HttpResponse.json(session(employee));
    }));
    const { user } = renderApp('/change-password');

    await user.type(await screen.findByLabelText('Current password'), 'temporary');
    await user.type(screen.getByLabelText('New password'), 'short');
    await user.click(screen.getByRole('button', { name: 'Save password' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/meets every rule/);
    expect(posts).toBe(0);
  });

  it('continues into the app once the password is changed', async () => {
    let body;
    server.use(signedInAs(mustChange), http.post(`${API}/auth/change-password`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(session(employee, 'token-2'));
    }));
    const { user } = renderApp('/change-password');

    await user.type(await screen.findByLabelText('Current password'), 'temporary');
    await user.type(screen.getByLabelText('New password'), 'BrandNewPass1');
    await user.click(screen.getByRole('button', { name: 'Save password' }));

    await waitFor(() => expect(currentUrl().pathname).toBe('/'));
    expect(body).toEqual({ current_password: 'temporary', new_password: 'BrandNewPass1' });
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeInTheDocument();
  });
});

describe('accessibility', () => {
  // jsdom cannot compute colours, so contrast is left to manual and browser checks.
  const options = { rules: { 'color-contrast': { enabled: false } } };

  it('login page has no axe violations', async () => {
    server.use(http.post(`${API}/auth/refresh`, () => apiError(401, 'unauthenticated', 'No session.')));
    const { container } = renderApp('/login');
    await screen.findByRole('button', { name: /sign in/i });
    expect(await axe(container, options)).toHaveNoViolations();
  });

  it('change-password page has no axe violations', async () => {
    server.use(signedInAs(mustChange));
    const { container } = renderApp('/change-password');
    await screen.findByRole('heading', { name: 'Change password' });
    expect(await axe(container, options)).toHaveNoViolations();
  });
});

describe('sign up name', () => {
  const signedOut = () => http.post(`${API}/auth/refresh`, () => apiError(401, 'unauthenticated', 'No session.'));

  async function fill(user, name) {
    await user.type(await screen.findByLabelText('Full name'), name);
    await user.type(screen.getByLabelText('Work email'), 'new.person@acme.inc');
    await user.type(screen.getByLabelText('Password'), 'Valid-Passphrase-734!');
    await user.type(screen.getByLabelText('Confirm password'), 'Valid-Passphrase-734!');
    await user.click(screen.getByRole('button', { name: 'Create account' }));
  }

  it.each(['12345', 'John123', 'user_1'])('refuses "%s" before calling the API', async (name) => {
    const calls = [];
    server.use(signedOut(), http.post(`${API}/auth/register`, async ({ request }) => {
      calls.push(await request.json());
      return HttpResponse.json(employee, { status: 201 });
    }));
    const { user } = renderApp('/register');
    await fill(user, name);
    expect(await screen.findByText(/Use letters only/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it('accepts a real name and sends it with tidy spacing', async () => {
    const calls = [];
    server.use(signedOut(), http.post(`${API}/auth/register`, async ({ request }) => {
      calls.push(await request.json());
      return HttpResponse.json(employee, { status: 201 });
    }));
    const { user } = renderApp('/register');
    await fill(user, "  Anne-Marie   O'Neil ");
    expect(await screen.findByText('Account created')).toBeInTheDocument();
    expect(calls[0].full_name).toBe("Anne-Marie O'Neil");
  });
});
