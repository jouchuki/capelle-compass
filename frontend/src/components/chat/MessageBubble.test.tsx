import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import MessageBubble from './MessageBubble';
import AnalysisHandoffCard from './AnalysisHandoffCard';
import type { AnalysisResult, ChatMessage } from '../../types';
import sessionD1a378 from '../../test/fixtures/session-d1a378.json';

const blockReport = sessionD1a378 as unknown as AnalysisResult;

function assistantMessageWith(analysis: AnalysisResult): ChatMessage {
  return {
    id: 'm1',
    session_id: 's1',
    role: 'assistant',
    content: 'klaar',
    status: 'complete',
    metadata: { type: 'analysis_result', analysis },
    created_at: '2026-01-01T00:00:00Z',
  };
}

describe('MessageBubble with a v2 block report (no sections)', () => {
  it('renders the assistant bubble + handoff card without throwing', () => {
    const { getByText } = render(
      <MessageBubble message={assistantMessageWith(blockReport)} onOpenReport={() => {}} />,
    );
    // the handoff card CTA proves hasAnalysis was true and nothing threw
    expect(getByText(/Open Rapport/i)).toBeTruthy();
  });
});

describe('AnalysisHandoffCard with a v2 block report', () => {
  it('renders source/section counts without reading .sections of undefined', () => {
    expect(() =>
      render(<AnalysisHandoffCard analysis={blockReport} onOpen={() => {}} />),
    ).not.toThrow();
  });
});
