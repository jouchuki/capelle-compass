import { describe, it, expect } from 'vitest';
import { reducer, INITIAL_STATE, MAX_FOCUS_NODES } from './chatReducer';
import type { ChatState, ScopedNode } from './chatReducer';
import type { ElicitationEvent, WSEvent } from '../types';

function progress(
  status: 'running' | 'done',
  tool: string,
  action: string,
  messageId = 'm-assistant',
): WSEvent {
  return { type: 'progress', message_id: messageId, tool, action, status };
}

function elicitation(
  questionId: string,
  overrides: Partial<ElicitationEvent> = {},
): ElicitationEvent {
  return {
    type: 'elicitation',
    message_id: 'm-assistant',
    question_id: questionId,
    question: `vraag ${questionId}`,
    options: ['a', 'b'],
    allow_free_text: true,
    ...overrides,
  };
}

describe('chat reducer — concurrent elicitations (parallel subagent asks)', () => {
  it('keeps every concurrent ask instead of overwriting the previous one', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_elicitation', event: elicitation('q1') });
    state = reducer(state, { type: 'ws_elicitation', event: elicitation('q2') });
    state = reducer(state, { type: 'ws_elicitation', event: elicitation('q3') });

    expect(state.elicitations.map((e) => e.question_id)).toEqual(['q1', 'q2', 'q3']);
  });

  it('dedups a re-broadcast of the same question_id', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_elicitation', event: elicitation('q1') });
    state = reducer(state, { type: 'ws_elicitation', event: elicitation('q1') });

    expect(state.elicitations).toHaveLength(1);
  });

  it('answering one question removes only that one and records the Q&A', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_elicitation', event: elicitation('q1') });
    state = reducer(state, { type: 'ws_elicitation', event: elicitation('q2') });

    state = reducer(state, {
      type: 'answer_elicitation',
      questionId: 'q1',
      answer: 'mijn antwoord',
    });

    expect(state.elicitations.map((e) => e.question_id)).toEqual(['q2']);
    expect(state.answeredElicitations).toEqual([
      { message_id: 'm-assistant', question: 'vraag q1', answer: 'mijn antwoord' },
    ]);
  });

  it('clears the run’s pending asks when the message completes', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_elicitation', event: elicitation('q1') });
    state = reducer(state, { type: 'ws_elicitation', event: elicitation('q2') });

    state = reducer(state, {
      type: 'ws_complete',
      event: { type: 'message_complete', message_id: 'm-assistant', content: 'klaar' },
    });

    expect(state.elicitations).toEqual([]);
  });
});

describe('chat reducer — activity steps (start → done, bucketed by message)', () => {
  it('a running event appends a running step to the message bucket', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_progress', event: progress('running', 'Read', 'Leest: a.csv') });

    const bucket = state.stepsByMessage['m-assistant'] ?? [];
    expect(bucket).toHaveLength(1);
    expect(bucket[0]).toMatchObject({ tool: 'Read', action: 'Leest: a.csv', status: 'running' });
  });

  it('a matching done event flips the open step to done (no new row)', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_progress', event: progress('running', 'Read', 'Leest: a.csv') });
    state = reducer(state, { type: 'ws_progress', event: progress('done', 'Read', 'Leest: a.csv') });

    const bucket = state.stepsByMessage['m-assistant'] ?? [];
    expect(bucket).toHaveLength(1);
    expect(bucket[0]?.status).toBe('done');
  });

  it('done flips only the matching row, leaving other steps running', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_progress', event: progress('running', 'Read', 'Leest: a.csv') });
    state = reducer(state, { type: 'ws_progress', event: progress('running', 'Grep', "Zoekt: 'x'") });
    state = reducer(state, { type: 'ws_progress', event: progress('done', 'Read', 'Leest: a.csv') });

    const bucket = state.stepsByMessage['m-assistant'] ?? [];
    expect(bucket.map((s) => s.status)).toEqual(['done', 'running']);
  });

  it('a done event with no open match appends an already-done row', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_progress', event: progress('done', 'Write', 'Schrijft: rapport') });

    const bucket = state.stepsByMessage['m-assistant'] ?? [];
    expect(bucket).toHaveLength(1);
    expect(bucket[0]).toMatchObject({ status: 'done', action: 'Schrijft: rapport' });
  });

  it('keeps each run’s steps in its own message bucket', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_progress', event: progress('running', 'Read', 'a', 'm1') });
    state = reducer(state, { type: 'ws_progress', event: progress('running', 'Read', 'b', 'm2') });

    expect(state.stepsByMessage['m1']).toHaveLength(1);
    expect(state.stepsByMessage['m2']).toHaveLength(1);
  });

  it('keeps a completed message’s steps intact (for the collapsed summary)', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_progress', event: progress('done', 'Read', 'a') });
    state = reducer(state, {
      type: 'ws_complete',
      event: { type: 'message_complete', message_id: 'm-assistant', content: 'klaar' },
    });

    expect(state.stepsByMessage['m-assistant']).toHaveLength(1);
  });

  it('step ids are unique across rows', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'ws_progress', event: progress('running', 'Read', 'a') });
    state = reducer(state, { type: 'ws_progress', event: progress('running', 'Read', 'b') });

    const ids = (state.stepsByMessage['m-assistant'] ?? []).map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe('chat reducer — follow-up scope ("Verdiep dit", multi-node)', () => {
  function scoped(id: string): ScopedNode {
    return { id, claim: `claim ${id}` };
  }

  it('adds a node to the scope', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'scope_add', node: scoped('n1') });

    expect(state.scopedNodes).toEqual([{ id: 'n1', claim: 'claim n1' }]);
  });

  it('adds a second node WITHOUT replacing the first', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'scope_add', node: scoped('n1') });
    state = reducer(state, { type: 'scope_add', node: scoped('n2') });

    expect(state.scopedNodes.map((n) => n.id)).toEqual(['n1', 'n2']);
  });

  it('dedups a re-add of the same node id', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'scope_add', node: scoped('n1') });
    state = reducer(state, { type: 'scope_add', node: scoped('n1') });

    expect(state.scopedNodes).toHaveLength(1);
  });

  it('removing one node leaves the others in place', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'scope_add', node: scoped('n1') });
    state = reducer(state, { type: 'scope_add', node: scoped('n2') });
    state = reducer(state, { type: 'scope_add', node: scoped('n3') });

    state = reducer(state, { type: 'scope_remove', nodeId: 'n2' });

    expect(state.scopedNodes.map((n) => n.id)).toEqual(['n1', 'n3']);
  });

  it(`caps the scope at MAX_FOCUS_NODES (${MAX_FOCUS_NODES})`, () => {
    let state: ChatState = INITIAL_STATE;
    for (let i = 1; i <= MAX_FOCUS_NODES + 2; i += 1) {
      state = reducer(state, { type: 'scope_add', node: scoped(`n${i}`) });
    }

    expect(state.scopedNodes).toHaveLength(MAX_FOCUS_NODES);
    expect(state.scopedNodes.map((n) => n.id)).toEqual(['n1', 'n2', 'n3', 'n4', 'n5']);
  });

  it('scope_clear empties the list (post-send / session switch)', () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'scope_add', node: scoped('n1') });
    state = reducer(state, { type: 'scope_add', node: scoped('n2') });

    state = reducer(state, { type: 'scope_clear' });

    expect(state.scopedNodes).toEqual([]);
  });

  it("a messages 'set' (session reload) does not wipe the scope", () => {
    let state: ChatState = INITIAL_STATE;
    state = reducer(state, { type: 'scope_add', node: scoped('n1') });

    state = reducer(state, { type: 'set', messages: [] });

    expect(state.scopedNodes.map((n) => n.id)).toEqual(['n1']);
  });
});
