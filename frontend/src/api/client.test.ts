import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { sendMessage } from './client';

/**
 * Wire-payload contract for node-scoped follow-ups ("Verdiep dit"):
 *
 *   - no scoped nodes  → neither `node_id` nor `node_ids` on the body
 *   - exactly 1 node   → singular `node_id` (back-compat with the deployed
 *                        backend, which predates `node_ids`)
 *   - 2..5 nodes       → plural `node_ids`, NO `node_id`
 */
describe('sendMessage — focus-node payload selection', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ user_message_id: 'u1', assistant_message_id: 'a1', status: 'ok' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function sentBody(): Record<string, unknown> {
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    return JSON.parse(init.body as string) as Record<string, unknown>;
  }

  it('no scoped nodes → payload is byte-identical to a plain message', async () => {
    await sendMessage('s1', 'hoi');
    expect(sentBody()).toEqual({ content: 'hoi' });
  });

  it('an empty array behaves like no scoped nodes', async () => {
    await sendMessage('s1', 'hoi', []);
    expect(sentBody()).toEqual({ content: 'hoi' });
  });

  it('exactly one scoped node → node_id set, node_ids ABSENT', async () => {
    await sendMessage('s1', 'verdiep dit', ['n1']);
    const body = sentBody();
    expect(body.node_id).toBe('n1');
    expect(body).not.toHaveProperty('node_ids');
  });

  it('two scoped nodes → node_ids set, node_id ABSENT', async () => {
    await sendMessage('s1', 'verdiep deze twee', ['n1', 'n2']);
    const body = sentBody();
    expect(body.node_ids).toEqual(['n1', 'n2']);
    expect(body).not.toHaveProperty('node_id');
  });

  it('five scoped nodes ride along in order', async () => {
    await sendMessage('s1', 'verdiep alles', ['n1', 'n2', 'n3', 'n4', 'n5']);
    expect(sentBody().node_ids).toEqual(['n1', 'n2', 'n3', 'n4', 'n5']);
  });

  it('posts to the session messages endpoint', async () => {
    await sendMessage('s1', 'hoi', ['n1']);
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/chat/sessions/s1/messages');
  });
});
