import { act, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { API, page, server } from '../test/server';
import { currentUrl, renderApp } from '../test/render';

const bell = () => screen.getByRole('link', { name: /^Notifications/ });

function setVisibility(state) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
}

describe('notification bell', () => {
  let calls;
  let unread;

  beforeEach(() => {
    calls = 0;
    unread = 3;
    server.use(http.get(`${API}/notifications/unread-count`, () => {
      calls += 1;
      return HttpResponse.json({ unread });
    }));
    // Only the poll interval is faked; promises and other timers run normally.
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    setVisibility('visible');
  });

  afterEach(() => {
    vi.useRealTimers();
    setVisibility('visible');
  });

  async function settled() {
    // Wait until the mount-time checks have finished, then return the call count.
    await screen.findByRole('link', { name: 'Notifications, 3 unread' });
    const before = calls;
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls).toBe(before);
    return calls;
  }

  it('shows the unread count and polls every 30 seconds while visible', async () => {
    renderApp('/');
    const start = await settled();
    expect(bell()).toHaveTextContent('3');

    await act(() => vi.advanceTimersByTime(29_000));
    expect(calls).toBe(start);

    unread = 120;
    await act(() => vi.advanceTimersByTime(1_000));
    await vi.waitFor(() => expect(calls).toBe(start + 1));
    expect(await screen.findByRole('link', { name: 'Notifications, 120 unread' })).toHaveTextContent('99+');

    await act(() => vi.advanceTimersByTime(60_000));
    await vi.waitFor(() => expect(calls).toBe(start + 3));
  });

  it('does not poll while the tab is hidden, and checks again when it becomes visible', async () => {
    renderApp('/');
    const start = await settled();

    setVisibility('hidden');
    document.dispatchEvent(new Event('visibilitychange'));
    await act(() => vi.advanceTimersByTime(90_000));
    expect(calls).toBe(start);

    setVisibility('visible');
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.waitFor(() => expect(calls).toBe(start + 1));
  });

  it('re-checks as soon as notifications are marked read', async () => {
    server.use(
      http.get(`${API}/notifications`, () => HttpResponse.json(page([
        { id: 'n-1', incident_id: 'i-1', message: 'INC-0001 was resolved', read: false, created_at: '2026-10-20T08:00:00Z' },
      ]))),
      http.post(`${API}/notifications/read-all`, () => {
        unread = 0;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = renderApp('/notifications');
    await settled();

    await user.click(screen.getByRole('button', { name: 'Mark all as read' }));
    // No poll tick or navigation needed.
    expect(await screen.findByRole('link', { name: 'Notifications' })).not.toHaveTextContent(/\d/);
  });

  it('stops polling after sign-out unmounts the layout', async () => {
    server.use(http.post(`${API}/auth/logout`, () => new HttpResponse(null, { status: 204 })));
    const { user } = renderApp('/');
    const start = await settled();

    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await screen.findByRole('button', { name: /sign in/i });
    await act(() => vi.advanceTimersByTime(90_000));
    expect(calls).toBe(start);
  });
});

describe('mobile More sheet', () => {
  async function openSheet(path = '/') {
    const view = renderApp(path);
    const more = await screen.findByRole('button', { name: 'More' });
    expect(more).toHaveAttribute('aria-expanded', 'false');
    await view.user.click(more);
    const sheet = screen.getByRole('dialog', { name: 'All pages' });
    return { ...view, more, sheet };
  }

  it('opens as a modal dialog with focus inside, and keeps Tab inside it', async () => {
    const { user, more, sheet } = await openSheet();
    expect(more).toHaveAttribute('aria-expanded', 'true');
    expect(more).toHaveAttribute('aria-controls', 'more-menu');
    expect(sheet).toHaveAttribute('aria-modal', 'true');
    expect(sheet).toHaveFocus();

    const search = within(sheet).getByRole('searchbox');
    const links = within(sheet).getAllByRole('link');
    await user.tab();
    expect(search).toHaveFocus();
    await user.tab({ shift: true });
    expect(links.at(-1)).toHaveFocus();
    await user.tab();
    expect(search).toHaveFocus();
  });

  it('closes on Escape, even with focus on the More button, and returns focus to it', async () => {
    const { user, more } = await openSheet();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(more).toHaveFocus();
    expect(more).toHaveAttribute('aria-expanded', 'false');

    await user.click(more);
    act(() => more.focus());
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(more).toHaveFocus();
  });

  it('closes with its Close button and after navigating away', async () => {
    const { user, more, sheet } = await openSheet('/incidents');
    await user.click(within(sheet).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(more).toHaveFocus();

    await user.click(more);
    // A link outside the sheet (the logo) does not close it itself; the route change does.
    await user.click(screen.getByRole('link', { name: /ACME Incidents/ }));
    expect(currentUrl().pathname).toBe('/');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('theme toggle', () => {
  afterEach(() => {
    window.localStorage.removeItem('acme-theme');
    delete document.documentElement.dataset.theme;
  });

  it('cycles System, Light and Dark and remembers the choice', async () => {
    renderApp('/');
    const toggle = await screen.findByRole('button', { name: /^Theme: System/ });

    toggle.click();
    await screen.findByRole('button', { name: /^Theme: Light/ });
    expect(document.documentElement.dataset.theme).toBe('light');

    screen.getByRole('button', { name: /^Theme: Light/ }).click();
    await screen.findByRole('button', { name: /^Theme: Dark/ });
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem('acme-theme')).toBe('dark');

    screen.getByRole('button', { name: /^Theme: Dark/ }).click();
    await screen.findByRole('button', { name: /^Theme: System/ });
    expect(window.localStorage.getItem('acme-theme')).toBe('system');
  });
});
