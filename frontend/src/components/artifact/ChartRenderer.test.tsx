import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import ChartRenderer from './ChartRenderer';
import type { ChartSpec } from '../../types';

describe('ChartRenderer fallback', () => {
  it('renders a table when the spec fails its contract', () => {
    const bad: ChartSpec = {
      kind: 'bar',
      data: [{ wijk: 'A', score: 6 }],
      columns: [
        { key: 'wijk', label: 'Wijk', type: 'string' },
        { key: 'score', label: 'Score', type: 'number' },
      ],
      x: undefined, // missing x -> contract fails -> table
      y: 'score',
    };
    const { container } = render(<ChartRenderer spec={bad} />);
    expect(container.querySelector('table')).not.toBeNull();
  });
});
