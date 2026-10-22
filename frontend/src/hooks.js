import { useCallback, useEffect, useState } from 'react';

let toastCount = 0;

/**
 * State for <Toast>. `show(text)` makes a new { text, id } every time, so a repeated
 * message is announced again and gets its own timer. Returns [toast, show, setToast].
 */
export function useToast() {
  const [toast, setToast] = useState(null);
  const show = useCallback((text) => {
    toastCount += 1;
    setToast({ text, id: toastCount });
  }, []);
  return [toast, show, setToast];
}

/**
 * Load data from an async function. It re-runs when `deps` change or `reload()` is called;
 * stale responses from an earlier run are ignored.
 */
export function useLoad(load, deps) {
  const [tick, setTick] = useState(0);
  const key = JSON.stringify(deps) + tick;
  const [state, setState] = useState({ key: null, data: null, error: null });

  useEffect(() => {
    let live = true;
    load().then(
      (data) => live && setState({ key, data, error: null }),
      (error) => live && setState({ key, data: null, error }),
    );
    return () => {
      live = false;
    };
    // `key` captures `deps`; `load` is recreated on every render by design.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return {
    data: state.data,
    error: state.error,
    loading: state.key !== key,
    reload: () => setTick((t) => t + 1),
  };
}

/**
 * Page through a list that is already in memory, `size` items at a time.
 * `pagerProps` feed <Pager>; show it only when `pages > 1`. The page is clamped,
 * so a list that shrinks (e.g. after an assignment) never lands on an empty page.
 */
export function useClientPage(items, size) {
  const [page, setPage] = useState(1);
  const pages = Math.max(1, Math.ceil(items.length / size));
  const current = Math.min(page, pages);
  return {
    shown: items.slice((current - 1) * size, current * size),
    pages,
    reset: () => setPage(1),
    pagerProps: { data: { total: items.length, page_size: size }, page: current, pages, setPage },
  };
}
