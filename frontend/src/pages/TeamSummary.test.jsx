import { screen, waitFor } from '@testing-library/react';
import { delay, http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { API, employee, server, signedInAs } from '../test/server';
import { renderApp } from '../test/render';

const summary = (resolved) => ({
  team: { name: 'AV team', members_on_duty: 2 },
  totals: { resolved: { value: resolved, previous: 0 } },
  engineers: [],
  charts: {},
});

describe('team summary', () => {
  it('marks the old numbers as updating while another period loads', async () => {
    server.use(
      signedInAs({ ...employee, roles: ['lead'] }),
      http.get(`${API}/dashboard/team`, async ({ request }) => {
        const period = new URL(request.url).searchParams.get('period');
        if (period === 'week') return HttpResponse.json(summary(4));
        await delay(100);
        return HttpResponse.json(summary(17));
      }),
    );
    const { user } = renderApp('/team');
    expect(await screen.findByText('4')).toBeInTheDocument();
    const body = document.querySelector('.ts-body');
    expect(body).toHaveAttribute('aria-busy', 'false');

    await user.click(screen.getByRole('button', { name: 'Month' }));
    expect(body).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText('Updating…')).toBeInTheDocument();

    expect(await screen.findByText('17')).toBeInTheDocument();
    await waitFor(() => expect(body).toHaveAttribute('aria-busy', 'false'));
    expect(screen.queryByText('Updating…')).not.toBeInTheDocument();
  });
});
