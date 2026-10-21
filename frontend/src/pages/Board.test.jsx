import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { admin, API, apiError, incident, page, server, signedInAs } from '../test/server';
import { renderApp } from '../test/render';

const tickets = [
  incident({ id: 'i-open', number: 'INC-0001', status: 'Open', updated_at: '2026-10-20T09:00:00.000001Z' }),
  incident({ id: 'i-prog', number: 'INC-0002', status: 'In Progress', title: 'Leaking tap' }),
  incident({ id: 'i-res', number: 'INC-0003', status: 'Resolved', title: 'Broken chair' }),
  incident({ id: 'i-new', number: 'INC-0004', status: 'Open', assigned_to: null, title: 'Flickering light' }),
];

function boardApi({ transition } = {}) {
  const transitions = [];
  server.use(
    signedInAs(admin),
    http.get(`${API}/incidents`, ({ request }) => {
      const statuses = new URL(request.url).searchParams.getAll('status');
      return HttpResponse.json(page(tickets.filter((t) => statuses.includes(t.status))));
    }),
    http.post(`${API}/incidents/:id/transitions`, async ({ request, params }) => {
      const call = { id: params.id, ifMatch: request.headers.get('If-Match'), body: await request.json() };
      transitions.push(call);
      return transition ? transition(call) : HttpResponse.json({ ...tickets.find((t) => t.id === params.id), ...call.body });
    }),
  );
  return transitions;
}

async function openBoard(options) {
  const transitions = boardApi(options);
  const view = renderApp('/board');
  await screen.findByText('INC-0001');
  return { ...view, transitions };
}

const column = (name) => screen.getByRole('region', { name: new RegExp(`^${name}`) });
const card = (number) => screen.getByText(number).closest('li');
const dataTransfer = () => ({ data: {}, setData(k, v) { this.data[k] = v; }, getData(k) { return this.data[k]; } });

/** dragover is cancelled (preventDefault) only over a column that accepts the drop. */
function acceptsDrop(name, transfer) {
  return !fireEvent.dragOver(column(name), { dataTransfer: transfer });
}

describe('drag and drop', () => {
  it('accepts only the moves in the transition table', async () => {
    await openBoard();
    const transfer = dataTransfer();
    fireEvent.dragStart(card('INC-0001'), { dataTransfer: transfer });

    expect(acceptsDrop('In progress', transfer)).toBe(true);
    expect(acceptsDrop('Resolved', transfer)).toBe(false);
    expect(acceptsDrop('Blocked', transfer)).toBe(false);
    expect(acceptsDrop('Open', transfer)).toBe(false);
  });

  it('allows In progress to Blocked or Resolved but not back to Open', async () => {
    await openBoard();
    const transfer = dataTransfer();
    fireEvent.dragStart(card('INC-0002'), { dataTransfer: transfer });

    expect(acceptsDrop('Blocked', transfer)).toBe(true);
    expect(acceptsDrop('Resolved', transfer)).toBe(true);
    expect(acceptsDrop('Open', transfer)).toBe(false);
  });

  it('moves a card when dropped on an allowed column', async () => {
    const { transitions } = await openBoard();
    const transfer = dataTransfer();
    fireEvent.dragStart(card('INC-0001'), { dataTransfer: transfer });
    fireEvent.dragOver(column('In progress'), { dataTransfer: transfer });
    fireEvent.drop(column('In progress'), { dataTransfer: transfer });

    expect(await screen.findByText('INC-0001 moved to In progress.')).toBeInTheDocument();
    expect(transitions).toEqual([
      { id: 'i-open', ifMatch: '2026-10-20T09:00:00.000001Z', body: { status: 'In Progress' } },
    ]);
  });

  it('ignores a drop on a disallowed column', async () => {
    const { transitions } = await openBoard();
    const transfer = dataTransfer();
    fireEvent.dragStart(card('INC-0001'), { dataTransfer: transfer });
    fireEvent.drop(column('Resolved'), { dataTransfer: transfer });

    // Give any stray request a chance to happen.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(transitions).toEqual([]);
  });

  it('does not let an unassigned ticket be started', async () => {
    await openBoard();
    expect(card('INC-0004')).toHaveAttribute('draggable', 'false');
    expect(within(card('INC-0004')).queryByRole('combobox')).not.toBeInTheDocument();
  });
});

describe('"Move to" menu', () => {
  it('lists only allowed targets for the card', async () => {
    await openBoard();
    const options = within(screen.getByLabelText('Move INC-0001 to')).getAllByRole('option').map((o) => o.textContent);
    expect(options).toEqual(['Move to…', 'In progress']);
    const progress = within(screen.getByLabelText('Move INC-0002 to')).getAllByRole('option').map((o) => o.textContent);
    expect(progress).toEqual(['Move to…', 'Blocked', 'Resolved']);
  });

  it('posts the transition with If-Match', async () => {
    const { user, transitions } = await openBoard();
    await user.selectOptions(screen.getByLabelText('Move INC-0001 to'), 'In progress');

    expect(await screen.findByText('INC-0001 moved to In progress.')).toBeInTheDocument();
    expect(transitions).toEqual([
      { id: 'i-open', ifMatch: '2026-10-20T09:00:00.000001Z', body: { status: 'In Progress' } },
    ]);
  });

  it('asks for a public note before resolving', async () => {
    const { user, transitions } = await openBoard();
    await user.selectOptions(screen.getByLabelText('Move INC-0002 to'), 'Resolved');

    const form = screen.getByRole('form', { name: 'Resolve INC-0002' });
    expect(within(form).getByRole('button', { name: 'Mark resolved' })).toBeDisabled();
    await user.type(within(form).getByLabelText(/Public note/), 'Replaced the washer.');
    await user.click(within(form).getByRole('button', { name: 'Mark resolved' }));

    await waitFor(() => expect(transitions).toHaveLength(1));
    expect(transitions[0]).toMatchObject({
      id: 'i-prog', body: { status: 'Resolved', note: 'Replaced the washer.', visibility: 'public' },
    });
  });

  it('shows the server message when the ticket changed underneath (409)', async () => {
    const { user } = await openBoard({
      transition: () => apiError(409, 'version_conflict', 'This ticket was changed by someone else.'),
    });
    await user.selectOptions(screen.getByLabelText('Move INC-0001 to'), 'In progress');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'INC-0001: This ticket was changed by someone else. The board has been refreshed.',
    );
  });
});
