import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import SleuthMascot from './SleuthMascot';

describe('SleuthMascot', () => {
  it('shows the honest cold-start caption', () => {
    const { getByText } = render(<SleuthMascot />);
    expect(getByText(/even speuren/i)).toBeTruthy();
  });
});
