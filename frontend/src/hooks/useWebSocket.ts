import { useEffect, useRef, useState } from 'react';
import { createWebSocket } from '../api/client';
import type { WSEvent } from '../types';

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30_000;

/**
 * Maintains a live WebSocket to the platform backend with exponential
 * backoff reconnect (1s → 2s → 4s → 8s → 16s, capped at 30s). Delivers EVERY
 * event to ``onEvent`` and exposes a `connected` flag the UI can use to decide
 * whether to fall back to polling.
 *
 * Events are pushed through a callback rather than surfaced as a single
 * "lastEvent" state slot: a run can produce several events in the same tick
 * (e.g. parallel subagents each firing a `capelle-ask` elicitation), and a
 * single state slot would collapse them to whichever arrived last. The
 * callback fires once per frame so no event is ever dropped.
 *
 * The reconnect machinery is built with refs instead of inter-useCallback
 * recursion to avoid temporal-dead-zone hazards and stale closures.
 */
export function useWebSocket(
  isAuthenticated: boolean,
  onEvent?: (event: WSEvent) => void,
) {
  const [connected, setConnected] = useState<boolean>(false);

  // Hold the latest callback in a ref so a new closure identity on each render
  // doesn't tear down and reopen the socket.
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef<number>(INITIAL_BACKOFF_MS);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const teardownRef = useRef<boolean>(false);

  useEffect(() => {
    teardownRef.current = false;

    function clearReconnectTimer() {
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    }

    function scheduleReconnect() {
      if (teardownRef.current) return;
      clearReconnectTimer();
      const delay = Math.min(backoffRef.current, MAX_BACKOFF_MS);
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS);
        openSocket();
      }, delay);
    }

    function openSocket() {
      if (teardownRef.current) return;
      if (!isAuthenticated) return;

      const ws = createWebSocket();
      if (!ws) {
        // No token available yet — retry at current backoff.
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        backoffRef.current = INITIAL_BACKOFF_MS;
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as WSEvent;
          onEventRef.current?.(data);
        } catch {
          // ignore malformed messages
        }
      };

      ws.onerror = () => {
        // onclose will fire right after — reconnect bookkeeping is there.
      };

      ws.onclose = (event) => {
        wsRef.current = null;
        setConnected(false);
        if (teardownRef.current) return;
        // Close code 4401 = backend detected the JWT expired mid-stream.
        // Reloading tears down the in-memory auth state; /api/auth/me
        // will then 401 and useAuth drops the user back to /login.
        if (event.code === 4401) {
          window.location.reload();
          return;
        }
        scheduleReconnect();
      };

      wsRef.current = ws;
    }

    if (!isAuthenticated) {
      clearReconnectTimer();
      wsRef.current?.close();
      wsRef.current = null;
      setConnected(false);
      return;
    }

    openSocket();

    return () => {
      teardownRef.current = true;
      clearReconnectTimer();
      wsRef.current?.close();
      wsRef.current = null;
      setConnected(false);
    };
  }, [isAuthenticated]);

  return { connected };
}
