import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useWebSocket } from './useWebSocket';
import * as client from '../api/client';
import type { WSEvent } from '../types';

/** Minimal controllable stand-in for the browser WebSocket. */
class FakeWebSocket {
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  close = vi.fn();

  emit(event: WSEvent): void {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
}

function makeEvent(questionId: string): WSEvent {
  return {
    type: 'elicitation',
    message_id: 'm-assistant',
    question_id: questionId,
    question: `vraag ${questionId}`,
    options: ['a', 'b'],
    allow_free_text: true,
  };
}

describe('useWebSocket — delivers every frame', () => {
  let fake: FakeWebSocket;

  beforeEach(() => {
    fake = new FakeWebSocket();
    vi.spyOn(client, 'createWebSocket').mockReturnValue(fake as unknown as WebSocket);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('hands every back-to-back frame to onEvent (no single-slot drops)', () => {
    const received: WSEvent[] = [];
    renderHook(() => useWebSocket(true, (e) => received.push(e)));

    act(() => {
      // Two concurrent elicitations from parallel subagents, delivered in the
      // same tick. The old single-`lastEvent`-slot model collapsed these to one.
      fake.emit(makeEvent('q1'));
      fake.emit(makeEvent('q2'));
    });

    expect(received.map((e) => e.question_id)).toEqual(['q1', 'q2']);
  });
});
