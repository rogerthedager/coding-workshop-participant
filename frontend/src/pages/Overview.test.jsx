import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { admin, API, incident, page, server, signedInAs } from '../test/server';
import { renderApp } from '../test/render';

const DAY = 86400000;
const ago = (days) => new Date(Date.now() - days * DAY).toISOString();

/** 10 critical tickets, 8 pending escalations and 5 long-blocked tickets: 23 items. */
function mockOverview() {
  const critical = Array.from({ length: 10 }, (_, i) => incident({
    id: `i-crit-${i}`, number: `INC-1${String(i).padStart(3, '0')}`, title: `Critical ${i}`, priority: 'Critical',
  }));
  const dashboard = {
    lifecycle: { resolved: { sample_count: 0, mean_seconds: null } },
    pending_escalations: Array.from({ length: 8 }, (_, i) => ({
      id: `i-esc-${i}`, number: `INC-2${String(i).padStart(3, '0')}`, reason: 'Urgent', requested_at: ago(1),
    })),
    blocked: Array.from({ length: 5 }, (_, i) => ({
      id: `i-blk-${i}`, number: `INC-3${String(i).padStart(3, '0')}`, blocked_reason: 'Parts', blocked_since: ago(5),
    })),
    hotspots: { floors: [] },
    recurring: [],
  };
  server.use(
    signedInAs(admin),
    http.get(`${API}/dashboard/admin`, () => HttpResponse.json(dashboard)),
    http.get(`${API}/incidents`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      return HttpResponse.json(params.get('priority') === 'Critical' ? page(critical) : page([], { total: 0 }));
    }),
  );
}

const section = () => screen.getByRole('region', { name: 'Needs a decision' });
const rows = () => within(section()).getAllByRole('link');

describe('overview needs a decision', () => {
  it('shows 10 items at a time and pages through the rest', async () => {
    mockOverview();
    const { user } = renderApp('/overview');
    expect(await screen.findByText('1–10 of 23')).toBeInTheDocument();
    expect(rows()).toHaveLength(10);
    expect(rows()[0]).toHaveTextContent('INC-1000');
    const previous = within(section()).getByRole('button', { name: 'Previous' });
    const next = within(section()).getByRole('button', { name: 'Next' });
    expect(previous).toBeDisabled();

    await user.click(next);
    expect(within(section()).getByText('11–20 of 23')).toBeInTheDocument();
    expect(rows()).toHaveLength(10);
    expect(rows()[0]).toHaveTextContent('INC-2000');

    await user.click(next);
    expect(within(section()).getByText('21–23 of 23')).toBeInTheDocument();
    expect(rows()).toHaveLength(3);
    expect(next).toBeDisabled();

    // A new period starts again from the first page.
    await user.click(screen.getByRole('button', { name: 'Last 7 days' }));
    expect(await within(section()).findByText('1–10 of 23')).toBeInTheDocument();
  });

  it('hides the pager when everything fits on one page', async () => {
    server.use(
      signedInAs(admin),
      http.get(`${API}/dashboard/admin`, () => HttpResponse.json({
        lifecycle: { resolved: { sample_count: 0, mean_seconds: null } },
        pending_escalations: [{ id: 'i-esc', number: 'INC-0009', reason: 'Urgent', requested_at: ago(1) }],
        blocked: [], hotspots: { floors: [] }, recurring: [],
      })),
    );
    renderApp('/overview');
    expect(await screen.findByText('INC-0009')).toBeInTheDocument();
    expect(within(section()).queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});
