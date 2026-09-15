import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchUsage, type QuotaSnapshot } from '../api/client';

interface UseQuotaOptions {
  /**
   * Changing this value forces a re-fetch. Consumers pass a bumping
   * counter (or any stable primitive) after events that should
   * invalidate the cached snapshot — e.g. a completed WS
   * ``message_complete`` or a closed 429 modal.
   */
  trigger?: unknown;
}

interface UseQuotaResult {
  snapshot: QuotaSnapshot | null;
  refresh: () => Promise<void>;
  loading: boolean;
}

/**
 * Expose the current per-user analysis quota to any React tree.
 *
 * Centralising the fetch here keeps the QuotaPill and the 429 modal
 * in sync with a single network call. We also re-fetch when the
 * browser tab regains focus — in a typical workday the user leaves
 * the tab idle for hours, and after midnight the server-side quota
 * resets; refreshing on focus avoids stale "0 over" states without
 * any polling.
 */
export function useQuota(options: UseQuotaOptions = {}): UseQuotaResult {
  const { trigger } = options;
  const [snapshot, setSnapshot] = useState<QuotaSnapshot | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  // Guard against setState-after-unmount when the component tree
  // tears down mid-fetch (e.g. user logs out while a refresh is in
  // flight).
  const mountedRef = useRef<boolean>(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const next = await fetchUsage();
      if (mountedRef.current) {
        setSnapshot(next);
      }
    } catch {
      // Swallow: the pill should never surface a hard error. A stale
      // snapshot is preferable to a broken sidebar, and the 429 modal
      // path owns the user-facing "limit reached" message.
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, []);

  // Initial fetch + re-fetch on trigger changes.
  useEffect(() => {
    void refresh();
  }, [refresh, trigger]);

  // Refresh on tab focus so a user returning after midnight sees
  // the fresh daily allowance instead of yesterday's counter.
  useEffect(() => {
    const onFocus = () => {
      void refresh();
    };
    window.addEventListener('focus', onFocus);
    return () => {
      window.removeEventListener('focus', onFocus);
    };
  }, [refresh]);

  return { snapshot, refresh, loading };
}
