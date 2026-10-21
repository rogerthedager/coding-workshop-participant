import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { API, apiError, employee, server, signedInAs } from '../test/server';
import { renderApp } from '../test/render';

async function openForm(user = employee, userOptions) {
  server.use(signedInAs(user));
  const view = renderApp('/report', { userOptions });
  // Wait for the directory (categories and location tree) to load.
  await screen.findByRole('option', { name: 'Headquarters' });
  return view;
}

const field = (label) => screen.getByLabelText(label);

describe('location cascade', () => {
  it('enables floor then seat as the reporter narrows the location', async () => {
    const { user } = await openForm();
    expect(field('Floor (optional)')).toBeDisabled();
    expect(field('Seat or room (optional)')).toBeDisabled();

    await user.selectOptions(field('Building'), 'Headquarters');
    expect(field('Floor (optional)')).toBeEnabled();
    expect(within(field('Floor (optional)')).getAllByRole('option').map((o) => o.textContent))
      .toEqual(['Whole building', 'Floor 1', 'Floor 2']);
    expect(field('Seat or room (optional)')).toBeDisabled();

    await user.selectOptions(field('Floor (optional)'), 'Floor 1');
    expect(within(field('Seat or room (optional)')).getAllByRole('option').map((o) => o.textContent))
      .toEqual(['No specific seat', 'HQ-101', 'HQ-102']);
    await user.selectOptions(field('Seat or room (optional)'), 'HQ-102');
    expect(field('Seat or room (optional)')).toHaveValue('s-102');
  });

  it('clears floor and seat when the building changes', async () => {
    const { user } = await openForm();
    await user.selectOptions(field('Building'), 'Headquarters');
    await user.selectOptions(field('Floor (optional)'), 'Floor 2');
    await user.selectOptions(field('Seat or room (optional)'), 'HQ-201');

    await user.selectOptions(field('Building'), 'Annex');
    expect(field('Floor (optional)')).toHaveValue('');
    expect(field('Seat or room (optional)')).toHaveValue('');
    expect(field('Seat or room (optional)')).toBeDisabled();
    expect(within(field('Floor (optional)')).getByRole('option', { name: 'Annex ground' })).toBeInTheDocument();
  });

  it('pre-fills the reporter’s default seat', async () => {
    await openForm({ ...employee, default_seat_id: 's-201' });
    await waitFor(() => expect(field('Seat or room (optional)')).toHaveValue('s-201'));
    expect(field('Building')).toHaveValue('b-hq');
    expect(field('Floor (optional)')).toHaveValue('f-hq2');
    // Already the default, so there is nothing to save.
    expect(screen.queryByLabelText(/use this seat as my default/i)).not.toBeInTheDocument();
  });

  it('ignores a default seat that is no longer in the active tree', async () => {
    await openForm({ ...employee, default_seat_id: 's-retired' });
    expect(field('Building')).toHaveValue('');
  });
});

describe('duplicate suggestions', () => {
  const similar = {
    id: 'i-9', number: 'INC-0009', title: 'Floor 1 is freezing', status: 'Open',
    location: { floor_name: 'Floor 1' }, age_seconds: 3600, following: false,
  };

  it('offers open tickets once category and floor are chosen, and follows one', async () => {
    const asked = [];
    let followed = null;
    server.use(
      http.get(`${API}/incidents/similar`, ({ request }) => {
        const url = new URL(request.url);
        asked.push([url.searchParams.get('category_id'), url.searchParams.get('floor_id')]);
        return HttpResponse.json({ items: [similar] });
      }),
      http.post(`${API}/incidents/:id/follow`, ({ params }) => {
        followed = params.id;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = await openForm();

    await user.selectOptions(field('Category'), 'HVAC and temperature');
    await user.selectOptions(field('Building'), 'Headquarters');
    expect(screen.queryByRole('heading', { name: 'Is it one of these?' })).not.toBeInTheDocument();
    expect(asked).toEqual([]);

    await user.selectOptions(field('Floor (optional)'), 'Floor 1');
    const region = await screen.findByRole('region', { name: 'Is it one of these?' });
    expect(asked).toEqual([['c-hvac', 'f-hq1']]);
    expect(within(region).getByText('Floor 1 is freezing')).toBeInTheDocument();

    await user.click(within(region).getByRole('button', { name: 'Follow' }));
    expect(await within(region).findByRole('button', { name: 'Following' })).toBeDisabled();
    expect(followed).toBe('i-9');
  });

  it('shows nothing when there are no similar tickets', async () => {
    const { user } = await openForm();
    await user.selectOptions(field('Category'), 'HVAC and temperature');
    await user.selectOptions(field('Building'), 'Headquarters');
    await user.selectOptions(field('Floor (optional)'), 'Floor 1');
    await waitFor(() => expect(screen.queryByText('Is it one of these?')).not.toBeInTheDocument());
  });
});

describe('submit', () => {
  async function fillAndSubmit(user) {
    await user.type(field('Title'), 'Projector will not turn on');
    await user.selectOptions(field('Category'), 'IT and AV equipment › Projector');
    // Choosing a category inserts a starter template, which must be filled in.
    expect(field('Description').value).toMatch(/^Issue: IT and AV equipment - Projector\nDevice/);
    await user.clear(field('Description'));
    await user.type(field('Description'), 'Room 2.14 projector shows no signal on HDMI.');
    await user.selectOptions(field('Building'), 'Headquarters');
    await user.selectOptions(field('Floor (optional)'), 'Floor 2');
    await user.click(screen.getByRole('button', { name: /^High/ }));
    await user.click(screen.getByRole('button', { name: 'Submit incident' }));
  }

  it('shows who the ticket was routed to', async () => {
    let body;
    server.use(http.post(`${API}/incidents`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({
        id: 'i-42', number: 'INC-0042',
        routing: { status: 'assigned', assignee: { full_name: 'Sam Fixit' }, team: { name: 'AV team' } },
      }, { status: 201 });
    }));
    const { user } = await openForm();
    await fillAndSubmit(user);

    expect(await screen.findByRole('heading', { name: 'INC-0042 created' })).toBeInTheDocument();
    expect(screen.getByText(/Assigned to Sam Fixit, AV team/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View ticket' })).toHaveAttribute('href', '/incidents/i-42');
    expect(body).toEqual({
      title: 'Projector will not turn on',
      description: 'Room 2.14 projector shows no signal on HDMI.',
      category_id: 'c-proj',
      building_id: 'b-hq',
      floor_id: 'f-hq2',
      requested_priority: 'High',
    });
  });

  it('says an admin will assign when routing falls back to triage', async () => {
    server.use(http.post(`${API}/incidents`, () =>
      HttpResponse.json({ id: 'i-43', number: 'INC-0043', routing: { status: 'triage' } }, { status: 201 })));
    const { user } = await openForm();
    await fillAndSubmit(user);
    expect(await screen.findByText(/An admin will assign an engineer/)).toBeInTheDocument();
  });

  it('keeps a previous report’s photo uploads out of the next report', async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let count = 0;
    server.use(
      http.post(`${API}/incidents`, () => {
        count += 1;
        return HttpResponse.json({ id: `i-4${count}`, number: `INC-004${count}`, routing: { status: 'triage' } }, { status: 201 });
      }),
      http.post(`${API}/incidents/i-41/attachments/presign`, async () => {
        await gate;
        return apiError(503, 'unavailable', 'Storage is down.');
      }),
    );
    const { user } = await openForm();
    await user.upload(field(/^Photos/), new File(['png'], 'leak.png', { type: 'image/png' }));
    await fillAndSubmit(user);
    expect(await screen.findByText('Uploading images…')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Report another' }));
    expect(screen.getByRole('button', { name: 'Submit incident' })).toBeEnabled();
    release();
    await new Promise((resolve) => setTimeout(resolve, 50));

    await fillAndSubmit(user);
    expect(await screen.findByRole('heading', { name: 'INC-0042 created' })).toBeInTheDocument();
    expect(screen.queryByText(/leak\.png/)).not.toBeInTheDocument();
  }, 15_000); // two full form fills

  it('blocks submit until required fields are valid', async () => {
    let posted = false;
    server.use(http.post(`${API}/incidents`, () => {
      posted = true;
      return HttpResponse.json({});
    }));
    const { user } = await openForm();
    await user.click(screen.getByRole('button', { name: 'Submit incident' }));
    expect(screen.getByText('Title must be 5 to 120 characters.')).toBeInTheDocument();
    expect(screen.getByText('Choose a category.')).toBeInTheDocument();
    expect(screen.getByText('Choose a building.')).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it('rejects a non-image photo before upload', async () => {
    // The picker's accept="image/*" is only a hint; the form checks the type itself.
    const { user } = await openForm(employee, { applyAccept: false });
    await user.upload(field(/^Photos/), new File(['%PDF'], 'scan.pdf', { type: 'application/pdf' }));
    expect(screen.getByRole('alert')).toHaveTextContent('scan.pdf: use a JPEG, PNG, WebP or HEIC image');
  });
});
