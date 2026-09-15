import { describe, it, expect } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import ActivityLog from './ActivityLog';
import ActivitySummary from './ActivitySummary';
import type { ActivityStep } from '../../types';

const steps: ActivityStep[] = [
  { id: '0', tool: 'Read', action: 'Leest: a.csv', status: 'done' },
  { id: '1', tool: 'Grep', action: "Zoekt: 'criminaliteit'", status: 'running' },
];

describe('ActivityLog', () => {
  it('renders one row per step with its action text', () => {
    const { getByText } = render(<ActivityLog steps={steps} />);
    expect(getByText('Leest: a.csv')).toBeTruthy();
    expect(getByText("Zoekt: 'criminaliteit'")).toBeTruthy();
  });

  it('marks done steps as voltooid and running steps as bezig', () => {
    const { getByLabelText } = render(<ActivityLog steps={steps} />);
    expect(getByLabelText('voltooid')).toBeTruthy();
    expect(getByLabelText('bezig')).toBeTruthy();
  });
});

describe('ActivitySummary', () => {
  it('shows a collapsed step count and hides the rows until expanded', () => {
    const { getByText, queryByText } = render(<ActivitySummary steps={steps} />);
    expect(getByText(/2 stappen/)).toBeTruthy();
    expect(queryByText('Leest: a.csv')).toBeNull();

    fireEvent.click(getByText(/2 stappen/));
    expect(getByText('Leest: a.csv')).toBeTruthy();
  });

  it('renders nothing when there are no steps', () => {
    const { container } = render(<ActivitySummary steps={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
