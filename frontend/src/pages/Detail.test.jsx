import { act, screen, waitFor, within } from '@testing-library/react';
import { delay, http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import { API, apiError, engineer, incident, server, signedInAs } from '../test/server';
import { currentUrl, renderApp } from '../test/render';

let tickets;
let transitions;
let notes;
let timeline;
beforeEach(() => {
  transitions = [];
  tickets = {
    'i-1': incident(),
    'i-2': incident({ id: 'i-2', number: 'INC-0002', title: 'Leaking tap in kitchen' }),
  };
  notes = { items: [], people: {} };
  timeline = { items: [], people: {} };
  server.use(
    http.get(`${API}/incidents/:id`, ({ params }) =>
      (tickets[params.id] ? HttpResponse.json(tickets[params.id]) : apiError(404, 'not_found', 'Incident not found'))),
    http.get(`${API}/incidents/:id/notes`, () => HttpResponse.json(notes)),
    http.get(`${API}/incidents/:id/timeline`, () => HttpResponse.json(timeline)),
    http.get(`${API}/incidents/:id/attachments`, () => HttpResponse.json({ items: [] })),
    http.post(`${API}/incidents/:id/transitions`, async ({ params, request }) => {
      transitions.push([params.id, await request.json()]);
      await delay(50);
      tickets[params.id] = { ...tickets[params.id], status: 'In Progress' };
      return HttpResponse.json(tickets[params.id]);
    }),
  );
});

async function openTicket(id = 'i-1') {
  const view = renderApp(`/incidents/${id}`);
  await screen.findByRole('heading', { name: tickets[id].title });
  return view;
}

describe('ticket actions', () => {
  it('lets the reporter cancel an Open ticket that routing already assigned', async () => {
    tickets['i-1'] = incident({ assigned_to: 'u-eng', permissions: { creator: true } });
    await openTicket();
    expect(screen.getByRole('button', { name: 'Cancel ticket' })).toBeInTheDocument();
  });

  it('sends one request when Start work is double-clicked', async () => {
    const { user } = await openTicket();
    await user.dblClick(screen.getByRole('button', { name: 'Start work' }));
    expect(await screen.findByText('Work started.')).toBeInTheDocument();
    expect(transitions).toEqual([['i-1', { status: 'In Progress' }]]);
  });

  it('keeps the submit button disabled until the reason is long enough', async () => {
    tickets['i-1'] = incident({ permissions: { creator: true } });
    const { user } = await openTicket();
    await user.click(screen.getByRole('button', { name: 'Request escalation' }));
    const send = screen.getByRole('button', { name: 'Send request' });
    expect(send).toBeDisabled();

    // Surrounding spaces do not count; the API trims them.
    await user.type(screen.getByLabelText(/more urgency/), '  urgent  ');
    expect(screen.getByText('At least 10 characters (6/10)')).toBeInTheDocument();
    expect(send).toBeDisabled();

    await user.type(screen.getByLabelText(/more urgency/), 'please');
    expect(screen.queryByText(/At least 10 characters/)).not.toBeInTheDocument();
    expect(send).toBeEnabled();
  });
});

describe('names', () => {
  it('shows the names the API sends to a non-admin instead of generic labels', async () => {
    server.use(signedInAs(engineer));
    tickets['i-1'] = incident({
      assigned_to: 'u-pat', created_by: 'u-emp', permissions: { staff: true, staff_read: true },
      people: { 'u-emp': 'Jamie Chen', 'u-pat': 'Pat Lee' },
    });
    notes = {
      items: [
        { id: 'n-1', author_id: 'u-emp', body: 'Still dark.', visibility: 'public', created_at: '2026-10-20T08:30:00Z' },
        { id: 'n-2', author_id: 'u-eng', body: 'On my way.', visibility: 'public', created_at: '2026-10-20T08:40:00Z' },
      ],
      people: { 'u-emp': 'Jamie Chen', 'u-eng': engineer.full_name },
    };
    timeline = {
      items: [{ id: 'e-1', type: 'assigned', actor_id: 'u-lead', payload: { assignee: 'u-pat' }, created_at: '2026-10-20T08:10:00Z' }],
      people: { 'u-lead': 'Morgan Lead', 'u-pat': 'Pat Lee' },
    };
    await openTicket();

    expect(screen.getByText('Pat Lee')).toBeInTheDocument(); // assignee fact
    expect(screen.getByText(/^Jamie Chen ·/)).toBeInTheDocument(); // reporter
    const noteList = screen.getByRole('region', { name: 'Notes' });
    expect(within(noteList).getByText('Jamie Chen')).toBeInTheDocument();
    expect(within(noteList).getByText('You')).toBeInTheDocument(); // the viewer's own note
    const events = screen.getByRole('complementary', { name: 'Timeline' });
    expect(within(events).getByText('Assigned to Pat Lee')).toBeInTheDocument();
    expect(within(events).getByText(/^Morgan Lead ·/)).toBeInTheDocument();
    expect(screen.queryByText(/Facilities team|Assigned engineer/)).not.toBeInTheDocument();
  });
});

describe('losing access after a change', () => {
  let returned;
  beforeEach(() => {
    returned = [];
    server.use(
      signedInAs(engineer),
      http.post(`${API}/incidents/:id/return-to-triage`, async ({ params, request }) => {
        returned.push(await request.json());
        delete tickets[params.id]; // unassigned, so the caller can no longer read it
        return HttpResponse.json({});
      }),
      http.get(`${API}/dashboard/team`, () => HttpResponse.json({
        team: { name: 'AV team', members_on_duty: 1 }, totals: { resolved: { value: 0, previous: 0 } }, engineers: [], charts: {},
      })),
    );
  });

  async function returnToTriage() {
    const view = await openTicket();
    await view.user.click(screen.getByRole('button', { name: 'Return to triage' }));
    const reason = screen.getByLabelText(/belong with the team/);
    await view.user.type(reason, 'This is a door lock, not AV');
    await view.user.click(within(reason.closest('form')).getByRole('button', { name: 'Return to triage' }));
    return view;
  }

  it('sends the assignee to their queue after returning a ticket to triage', async () => {
    tickets['i-1'] = incident({ assigned_to: 'u-eng', permissions: { staff: true, staff_read: true } });
    await returnToTriage();

    await screen.findByRole('heading', { name: 'My queue' });
    expect(currentUrl().pathname).toBe('/queue');
    expect(screen.getByText('Returned to triage. An admin will reassign it.')).toBeInTheDocument();
    expect(screen.queryByText('Ticket not found')).not.toBeInTheDocument();
    expect(returned).toEqual([{ reason: 'This is a door lock, not AV' }]);
  });

  it('sends a team lead to the team summary', async () => {
    server.use(signedInAs({ ...engineer, id: 'u-lead', roles: ['lead'] }));
    tickets['i-1'] = incident({ assigned_to: 'u-eng', permissions: { team_lead: true, staff_read: true } });
    await returnToTriage();

    await waitFor(() => expect(currentUrl().pathname).toBe('/team'));
    expect(await screen.findByText('Returned to triage. An admin will reassign it.')).toBeInTheDocument();
  });

  it('goes to My tickets with a notice when the reload after any change is not found', async () => {
    server.use(http.post(`${API}/incidents/:id/transitions`, ({ params }) => {
      delete tickets[params.id];
      return HttpResponse.json({});
    }));
    const { user } = await openTicket();
    await user.click(screen.getByRole('button', { name: 'Start work' }));

    expect(await screen.findByText('Work started. You no longer have access to that ticket.')).toBeInTheDocument();
    expect(currentUrl().pathname).toBe('/');
    expect(screen.queryByText('Ticket not found')).not.toBeInTheDocument();
  });
});

describe('moving between tickets', () => {
  it('starts fresh for the next ticket instead of keeping the open form', async () => {
    const { user } = await openTicket('i-1');
    await user.click(screen.getByRole('button', { name: 'Edit details' }));
    expect(screen.getByLabelText('Title')).toHaveValue('Projector will not turn on');

    act(() => {
      window.history.pushState(null, '', '/incidents/i-2');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await screen.findByRole('heading', { name: 'Leaking tap in kitchen' });
    await waitFor(() => expect(screen.queryByLabelText('Title')).not.toBeInTheDocument());
  });
});
