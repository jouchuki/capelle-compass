import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '../../api/client';
import type { AdminPagedResponse } from './adminFetch';

/**
 * Generic pagination state machine for an admin table tab.
 *
 * Caller hands us a fetcher keyed by 1-based page number; we keep the
 * page state, drive prev/next bounds against ``total / page_size``,
 * and surface any thrown ``ApiError`` (e.g. a 403 mid-session).
 */
export interface PagedFetchState<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: string | null;
  next: () => void;
  prev: () => void;
  refresh: () => void;
  /** Mutate the currently-displayed page in place (optimistic edits). */
  patchItems: (mutator: (items: T[]) => T[]) => void;
}

export function usePagedFetch<T>(
  fetcher: (page: number) => Promise<AdminPagedResponse<T>>,
): PagedFetchState<T> {
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetcher(page)
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        setTotal(res.total);
        setPageSize(res.page_size || 50);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) {
          const detail = err.detail;
          if (typeof detail === 'string') setError(detail);
          else if (detail && typeof detail === 'object' && 'message' in detail) {
            setError(String((detail as { message: unknown }).message));
          } else {
            setError(`HTTP ${err.status}`);
          }
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError('Onbekende fout');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fetcher, page, tick]);

  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));

  const next = useCallback(() => {
    setPage((p) => Math.min(totalPages, p + 1));
  }, [totalPages]);

  const prev = useCallback(() => {
    setPage((p) => Math.max(1, p - 1));
  }, []);

  const refresh = useCallback(() => {
    setTick((t) => t + 1);
  }, []);

  const patchItems = useCallback((mutator: (items: T[]) => T[]) => {
    setItems((prevItems) => mutator(prevItems));
  }, []);

  return { items, total, page, pageSize, loading, error, next, prev, refresh, patchItems };
}
