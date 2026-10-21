import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

/** Render the whole app at `path` (BrowserRouter reads window.location). */
export function renderApp(path = '/', { userOptions } = {}) {
  window.history.pushState(null, '', path);
  const user = userEvent.setup(userOptions);
  return { user, ...render(<App />) };
}

/** The current location as the app sees it. */
export const currentUrl = () => new URL(window.location.href);
