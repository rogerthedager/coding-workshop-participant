import { useSyncExternalStore } from 'react';

// Light/dark theme. The choice ("system", "light" or "dark") is kept per browser;
// "system" follows the OS setting. index.html applies it before first paint too.

const KEY = 'acme-theme';
const DARK = '(prefers-color-scheme: dark)';
export const THEMES = ['system', 'light', 'dark'];

const listeners = new Set();
let choice = read();

function read() {
  try {
    const saved = window.localStorage.getItem(KEY);
    return THEMES.includes(saved) ? saved : 'system';
  } catch {
    return 'system';
  }
}

const systemDark = () => Boolean(window.matchMedia?.(DARK).matches);

function apply() {
  const dark = choice === 'dark' || (choice === 'system' && systemDark());
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

export function setTheme(next) {
  choice = THEMES.includes(next) ? next : 'system';
  try {
    window.localStorage.setItem(KEY, choice);
  } catch {
    // Storage blocked (private mode): the choice still applies for this visit.
  }
  apply();
  listeners.forEach((fn) => fn());
}

/** Applies the saved theme and follows OS changes while the choice is "system". */
export function initTheme() {
  apply();
  window.matchMedia?.(DARK).addEventListener?.('change', apply);
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useTheme() {
  return useSyncExternalStore(subscribe, () => choice, () => 'system');
}
