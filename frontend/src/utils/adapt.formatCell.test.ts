import { describe, it, expect } from 'vitest';
import { formatCell } from './adapt';

describe('formatCell year vs number', () => {
  it('renders a year with NO thousands separator', () => {
    expect(formatCell(2025, { key: 'jaar', label: 'Jaar', type: 'year' })).toBe('2025');
    expect(formatCell(2021, { key: 'jaar', label: 'Jaar', type: 'year' })).toBe('2021');
  });
  it('still groups plain numbers (nl-NL) and appends unit', () => {
    expect(formatCell(235000, { key: 'b', label: 'B', type: 'number' })).toBe('235.000');
    expect(formatCell(28, { key: 'b', label: 'B', type: 'number', unit: '€ mln' })).toBe('28 € mln');
  });
});
