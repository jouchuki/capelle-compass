import { describe, it, expect, vi, beforeAll } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import Chat from './Chat';
import type { ActivityStep, ChatMessage, ElicitationEvent } from '../../types';

// jsdom doesn't implement Element.scrollTo, which Chat's auto-scroll effect calls.
beforeAll(() => {
  Element.prototype.scrollTo = vi.fn();
});

const assistant: ChatMessage = {
  id: 'm-assistant',
  session_id: 's1',
  role: 'assistant',
  content: '',
  status: 'thinking',
  metadata: null,
  created_at: '2026-01-01T00:00:00Z',
};

function ask(questionId: string, question: string): ElicitationEvent {
  return {
    type: 'elicitation',
    message_id: 'm-assistant',
    question_id: questionId,
    question,
    options: ['a', 'b'],
    allow_free_text: true,
  };
}

describe('Chat — concurrent elicitations', () => {
  it('renders one card per pending ask, not just the first', () => {
    const { getByText } = render(
      <Chat
        messages={[assistant]}
        stepsByMessage={{}}
        elicitations={[
          ask('q1', 'Welke gemeente voor Almere-slice?'),
          ask('q2', 'Welk jaar voor Zoetermeer-slice?'),
        ]}
        answeredElicitations={[]}
        sending={false}
        isArtifactOpen={false}
        onSend={() => {}}
        onOpenReport={() => {}}
        onElicitationAnswer={() => {}}
      />,
    );

    expect(getByText('Welke gemeente voor Almere-slice?')).toBeTruthy();
    expect(getByText('Welk jaar voor Zoetermeer-slice?')).toBeTruthy();
  });
});

const baseProps = {
  elicitations: [],
  answeredElicitations: [],
  sending: false,
  isArtifactOpen: false,
  onSend: () => {},
  onOpenReport: () => {},
  onElicitationAnswer: () => {},
};

describe('Chat — in-run activity', () => {
  it('shows the sleuth mascot during cold start (in-progress, no steps)', () => {
    const { getByText } = render(
      <Chat messages={[assistant]} stepsByMessage={{}} {...baseProps} />,
    );
    expect(getByText(/even speuren/i)).toBeTruthy();
  });

  it('replaces the mascot with the step list once a step lands', () => {
    const steps: ActivityStep[] = [
      { id: '0', tool: 'Read', action: 'Leest: a.csv', status: 'running' },
    ];
    const { getByText, queryByText } = render(
      <Chat messages={[assistant]} stepsByMessage={{ 'm-assistant': steps }} {...baseProps} />,
    );
    expect(getByText('Leest: a.csv')).toBeTruthy();
    expect(queryByText(/even speuren/i)).toBeNull();
  });

  it('shows a collapsed summary on a completed message', () => {
    const done: ChatMessage = { ...assistant, content: 'klaar', status: 'complete' };
    const steps: ActivityStep[] = [
      { id: '0', tool: 'Read', action: 'Leest: a.csv', status: 'done' },
      { id: '1', tool: 'Write', action: 'Schrijft: rapport', status: 'done' },
    ];
    const { getByText, queryByText } = render(
      <Chat messages={[done]} stepsByMessage={{ 'm-assistant': steps }} {...baseProps} />,
    );
    expect(getByText(/2 stappen/)).toBeTruthy();
    // collapsed by default — rows hidden until expanded
    expect(queryByText('Leest: a.csv')).toBeNull();
  });
});

describe('Chat — follow-up scope chips ("Verdiep dit")', () => {
  const scopedNodes = [
    { id: 'n1', claim: 'Groei concentreert zich in Fascinatio' },
    { id: 'n2', claim: 'Vergrijzing in Schenkel' },
  ];

  it('renders one removable chip per scoped node plus an n/5 counter', () => {
    const onRemoveScope = vi.fn();
    const { getByTestId } = render(
      <Chat
        messages={[]}
        stepsByMessage={{}}
        {...baseProps}
        scopedNodes={scopedNodes}
        onRemoveScope={onRemoveScope}
      />,
    );
    expect(getByTestId('scope-chip-n1').textContent).toContain(
      'Verdieping van: Groei concentreert zich in Fascinatio',
    );
    expect(getByTestId('scope-chip-n2').textContent).toContain(
      'Verdieping van: Vergrijzing in Schenkel',
    );
    expect(getByTestId('scope-counter').textContent).toBe('2/5');
  });

  it("a chip's × removes ONLY that node from the scope", () => {
    const onRemoveScope = vi.fn();
    const { getByTestId } = render(
      <Chat
        messages={[]}
        stepsByMessage={{}}
        {...baseProps}
        scopedNodes={scopedNodes}
        onRemoveScope={onRemoveScope}
      />,
    );
    const chip = getByTestId('scope-chip-n2');
    fireEvent.click(chip.querySelector('button')!);
    expect(onRemoveScope).toHaveBeenCalledTimes(1);
    expect(onRemoveScope).toHaveBeenCalledWith('n2');
  });

  it('truncates long claims in the chip to 50 characters', () => {
    const longClaim =
      'Een hele lange bewering die ver voorbij de vijftig tekens doorgaat en dus afgekapt wordt';
    const { getByTestId } = render(
      <Chat
        messages={[]}
        stepsByMessage={{}}
        {...baseProps}
        scopedNodes={[{ id: 'n9', claim: longClaim }]}
      />,
    );
    expect(getByTestId('scope-chip-n9').textContent).toContain(
      `${longClaim.slice(0, 50)}…`,
    );
  });

  it('renders no chip row at all when nothing is scoped (legacy rendering)', () => {
    const { queryByTestId } = render(
      <Chat messages={[]} stepsByMessage={{}} {...baseProps} />,
    );
    expect(queryByTestId('scope-chip-row')).toBeNull();
  });
});
