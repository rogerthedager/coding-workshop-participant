import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useToast } from '../hooks';
import { Toast } from './ui';

function Harness() {
  const [toast, show, setToast] = useToast();
  return (
    <>
      <button type="button" onClick={() => show('Saved.')}>Save</button>
      <Toast toast={toast} onDone={setToast} />
    </>
  );
}

describe('Toast', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('re-renders a repeated message and restarts its timer', () => {
    render(<Harness />);
    const region = screen.getByRole('status');

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const first = region.firstChild;
    expect(first).toHaveTextContent('Saved.');

    act(() => vi.advanceTimersByTime(4000));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    // A new node, so screen readers announce it again.
    expect(region.firstChild).toHaveTextContent('Saved.');
    expect(region.firstChild).not.toBe(first);

    // The first toast's timer must not clear the second one early.
    act(() => vi.advanceTimersByTime(4000));
    expect(region).toHaveTextContent('Saved.');
    act(() => vi.advanceTimersByTime(1000));
    expect(region).toBeEmptyDOMElement();
  });
});
