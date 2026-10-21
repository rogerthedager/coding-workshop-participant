import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { API, employee, incident, page, server, signedInAs } from '../test/server';
import { currentUrl, renderApp } from '../test/render';

let requests;
beforeEach(() => {
  requests = [];
  server.use(http.get(`${API}/incidents`, ({ request }) => {
    const url = new URL(request.url);
    requests.push(url.searchParams);
    const number = Number(url.searchParams.get('page') || 1);
    return HttpResponse.json(page([incident({ id: `i-${number}`, number: `INC-00${number}0` })], {
      total: 60, page: number, page_size: 25,
    }));
  }));
});

const lastRequest = () => requests.at(-1);
const statusButton = (name) => screen.getByRole('button', { name });
const urlStatuses = () => currentUrl().searchParams.getAll('status');

async function openList(path) {
  const view = renderApp(path);
  await screen.findByRole('heading', { name: 'Search' });
  await screen.findByRole('table');
  return view;
}

describe('URL state', () => {
  it('reads filters, repeated statuses and the page from the query string', async () => {
    await openList('/incidents?status=Open&status=Blocked&priority=High&page=2');

    expect(screen.getByRole('button', { name: 'Open', pressed: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Blocked', pressed: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resolved', pressed: false })).toBeInTheDocument();
    expect(screen.getByLabelText('Priority')).toHaveValue('High');
    expect(screen.getByText('26–50 of 60')).toBeInTheDocument();

    expect(lastRequest().getAll('status')).toEqual(['Open', 'Blocked']);
    expect(lastRequest().get('priority')).toBe('High');
    expect(lastRequest().get('page')).toBe('2');
  });

  it('writes status toggles to the URL and resets the page', async () => {
    const { user } = await openList('/incidents?status=Open&status=Blocked&page=2');

    await user.click(statusButton('Resolved'));
    expect(urlStatuses()).toEqual(['Open', 'Blocked', 'Resolved']);
    expect(currentUrl().searchParams.has('page')).toBe(false);
    await waitFor(() => expect(lastRequest().getAll('status')).toEqual(['Open', 'Blocked', 'Resolved']));
    expect(lastRequest().get('page')).toBe('1');

    await user.click(statusButton('Open'));
    expect(urlStatuses()).toEqual(['Blocked', 'Resolved']);
    expect(screen.getByRole('button', { name: 'Open', pressed: false })).toBeInTheDocument();
  });

  it('writes select filters and keeps the others', async () => {
    const { user } = await openList('/incidents?status=Open');
    await user.selectOptions(screen.getByLabelText('Priority'), 'Low');
    await user.selectOptions(screen.getByLabelText('SLA'), 'At risk');

    expect(Object.fromEntries(currentUrl().searchParams)).toEqual({ status: 'Open', priority: 'Low', sla_state: 'at_risk' });
    await waitFor(() => expect(lastRequest().get('sla_state')).toBe('at_risk'));

    await user.selectOptions(screen.getByLabelText('Priority'), 'Any');
    expect(currentUrl().searchParams.has('priority')).toBe(false);
  });

  it('pages through results via the URL', async () => {
    const { user } = await openList('/incidents?status=Open');
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(currentUrl().searchParams.get('page')).toBe('2');
    expect(await screen.findByText('INC-0020')).toBeInTheDocument();
    expect(lastRequest().getAll('status')).toEqual(['Open']);

    await user.click(screen.getByRole('button', { name: 'Previous' }));
    expect(currentUrl().searchParams.has('page')).toBe(false);
    expect(await screen.findByText('INC-0010')).toBeInTheDocument();
  });

  it('follows back and forward navigation', async () => {
    const { user } = await openList('/incidents');
    await user.click(statusButton('Blocked'));
    expect(urlStatuses()).toEqual(['Blocked']);

    window.history.back();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Blocked', pressed: false })).toBeInTheDocument());
    expect(urlStatuses()).toEqual([]);
  });

  it('writes the building filter to the URL and resets the page', async () => {
    const { user } = await openList('/incidents?status=Open&page=2');
    const select = screen.getByLabelText('Building');
    expect(select).toHaveValue('');
    await screen.findByRole('option', { name: 'Annex' });

    await user.selectOptions(select, 'Annex');
    expect(Object.fromEntries(currentUrl().searchParams)).toEqual({ status: 'Open', building_id: 'b-an' });
    await waitFor(() => expect(lastRequest().get('building_id')).toBe('b-an'));
    expect(lastRequest().get('page')).toBe('1');

    await user.selectOptions(select, 'All buildings');
    expect(currentUrl().searchParams.has('building_id')).toBe(false);
  });

  it('clears every filter', async () => {
    const { user } = await openList('/incidents?status=Open&priority=High&building_id=b-hq&category_id=c-hvac');
    expect(await screen.findByRole('button', { name: 'Remove filter Category' })).toHaveTextContent('Category: HVAC and temperature');
    await waitFor(() => expect(screen.getByLabelText('Building')).toHaveValue('b-hq'));

    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(currentUrl().search).toBe('');
    await waitFor(() => expect([...lastRequest().keys()]).toEqual(['page']));
  });
});

describe('Export CSV', () => {
  let clicked;
  beforeEach(() => {
    clicked = [];
    URL.createObjectURL = vi.fn(() => 'blob:incidents');
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() {
      clicked.push({ href: this.href, download: this.download });
    });
  });
  afterEach(() => {
    delete URL.createObjectURL;
    delete URL.revokeObjectURL;
  });

  it('exports with the current filters but not the page', async () => {
    let exported;
    server.use(http.get(`${API}/incidents/export.csv`, ({ request }) => {
      exported = new URL(request.url).searchParams;
      return new HttpResponse('number,title\nINC-0010,Projector\n', {
        headers: { 'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename="incidents-2026-10-21.csv"' },
      });
    }));
    const { user } = await openList('/incidents?status=Open&status=Blocked&q=projector&page=3');

    await user.click(screen.getByRole('button', { name: 'Export CSV' }));

    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(exported.getAll('status')).toEqual(['Open', 'Blocked']);
    expect(exported.get('q')).toBe('projector');
    expect(exported.has('page')).toBe(false);
    expect(clicked[0]).toEqual({ href: 'blob:incidents', download: 'incidents-2026-10-21.csv' });
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeEnabled();
  });

  it('shows the server error when the export fails', async () => {
    server.use(http.get(`${API}/incidents/export.csv`, () =>
      HttpResponse.json({ error: { code: 'too_many_rows', message: 'Narrow the filters to export.' } }, { status: 422 })));
    const { user } = await openList('/incidents');

    await user.click(screen.getByRole('button', { name: 'Export CSV' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Narrow the filters to export.');
    expect(clicked).toEqual([]);
  });
});

describe('sort', () => {
  it('sets the direction explicitly, so the queue preset (asc) cannot reverse other sorts', async () => {
    server.use(signedInAs({ ...employee, id: 'u-eng', roles: ['engineer'] }));
    const { user } = renderApp('/queue');
    await screen.findByRole('heading', { name: 'My queue' });
    await screen.findByRole('table');
    expect(lastRequest().get('sort')).toBe('sla_due');
    expect(lastRequest().get('direction')).toBe('asc');

    for (const [label, sort, direction] of [
      ['Newest', 'created', 'desc'],
      ['Priority', 'priority', 'desc'], // Critical has the highest rank
      ['Recently updated', 'updated', 'desc'],
      ['SLA due soonest', 'sla_due', 'asc'],
    ]) {
      await user.selectOptions(screen.getByLabelText('Sort'), label);
      expect(currentUrl().searchParams.get('direction')).toBe(direction);
      await waitFor(() => expect(lastRequest().get('sort')).toBe(sort));
      expect(lastRequest().get('direction')).toBe(direction);
    }
  });
});
