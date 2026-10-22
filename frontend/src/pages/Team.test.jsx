import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { admin, API, incident, page, server, signedInAs } from '../test/server';
import { renderApp } from '../test/render';

const DAY = 86400000;
const ago = (days) => new Date(Date.now() - days * DAY).toISOString();
const num = (prefix, i) => `INC-${prefix}${String(i).padStart(3, '0')}`;

const engineers = ['u-eng', 'u-eng2'].map((id, i) => ({
  id: `e-${i}`, user_id: id, user: { full_name: i ? 'Riley Park' : 'Sam Rivera' }, is_active: true,
  availability: 'Available', workload: 0, status_counts: {}, skills: [],
}));

/**
 * 23 unassigned Open tickets, 12 Blocked tickets and 13 In Progress tickets.
 * Blocked and In Progress are assigned, so "All team tickets" holds 25 rows,
 * 15 for Sam (u-eng) and 10 for Riley (u-eng2).
 */
function mockWorkload() {
  const items = [
    ...Array.from({ length: 23 }, (_, i) => incident({
      id: `i-un-${i}`, number: num(1, i), title: `Unassigned ${i}`, status: 'Open', assigned_to: null, priority: 'Medium',
    })),
    ...Array.from({ length: 12 }, (_, i) => incident({
      id: `i-blk-${i}`, number: num(2, i), title: `Blocked ${i}`, status: 'Blocked', assigned_to: i < 5 ? 'u-eng2' : 'u-eng',
      blocked_reason: 'Parts', blocked_since: ago(3),
    })),
    ...Array.from({ length: 13 }, (_, i) => incident({
      id: `i-wip-${i}`, number: num(3, i), title: `In progress ${i}`, status: 'In Progress', assigned_to: i < 5 ? 'u-eng2' : 'u-eng',
    })),
  ];
  server.use(
    signedInAs(admin),
    http.get(`${API}/engineers`, () => HttpResponse.json({ items: engineers })),
    http.get(`${API}/incidents`, () => HttpResponse.json(page(items, { page_size: 100 }))),
  );
}

const region = (name) => screen.getByRole('region', { name });
const next = (name) => within(region(name)).getByRole('button', { name: 'Next' });

describe('workload paging', () => {
  it('shows 10 waiting tickets per page', async () => {
    mockWorkload();
    const { user } = renderApp('/workload');
    const name = 'Waiting for an engineer';
    expect(await within(await screen.findByRole('region', { name })).findByText('1–10 of 23')).toBeInTheDocument();
    expect(within(region(name)).getAllByRole('combobox')).toHaveLength(10);
    // The header still counts every waiting ticket, not just this page.
    expect(within(region(name)).getByText('23 tickets')).toBeInTheDocument();

    await user.click(next(name));
    await user.click(next(name));
    expect(within(region(name)).getByText('21–23 of 23')).toBeInTheDocument();
    expect(within(region(name)).getAllByRole('combobox')).toHaveLength(3);
    expect(next(name)).toBeDisabled();
  });

  it('shows 10 blocked tickets per page', async () => {
    mockWorkload();
    const { user } = renderApp('/workload');
    const name = 'Blocked';
    expect(await within(await screen.findByRole('region', { name })).findByText('1–10 of 12')).toBeInTheDocument();
    expect(within(region(name)).getAllByRole('link', { name: 'Open' })).toHaveLength(10);

    await user.click(next(name));
    expect(within(region(name)).getByText('11–12 of 12')).toBeInTheDocument();
    expect(within(region(name)).getAllByRole('link', { name: 'Open' })).toHaveLength(2);
  });

  it('shows 20 team tickets per page and starts over when an engineer is picked', async () => {
    mockWorkload();
    const { user } = renderApp('/workload');
    const name = 'All team tickets';
    const body = async () => within(within(await screen.findByRole('region', { name: /tickets/ })).getByRole('table')).getAllByRole('row').slice(1);
    expect(await within(await screen.findByRole('region', { name })).findByText('1–20 of 25')).toBeInTheDocument();
    expect(await body()).toHaveLength(20);

    await user.click(next(name));
    expect(within(region(name)).getByText('21–25 of 25')).toBeInTheDocument();
    expect(await body()).toHaveLength(5);

    // Riley has 10 tickets: one page, no pager, back on page 1.
    await user.click(screen.getByRole('button', { name: /Riley Park/ }));
    const focused = region('Riley Park’s tickets');
    expect(await body()).toHaveLength(10);
    expect(within(focused).queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();

    // Back to the whole team: page 1 again.
    await user.click(within(focused).getByRole('button', { name: 'Show whole team' }));
    expect(within(region(name)).getByText('1–20 of 25')).toBeInTheDocument();
  });
});
