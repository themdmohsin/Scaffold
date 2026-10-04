/**
 * React Query client — the resilient data layer.
 *
 * - Per-query retry with exponential backoff (4xx never retried — those are
 *   caller errors; timeouts and 5xx retried twice).
 * - 15s stale time for most reads so switching between pages doesn't refetch
 *   storms; window refocus refetch stays on (cheap, keeps boards fresh).
 * - Realtime events bump the matching queries (debounced in lib/realtime).
 */

import { QueryClient } from "@tanstack/react-query";
import { ApiError, ApiTimeoutError } from "./api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: true,
      retry: (failureCount, error) => {
        if (failureCount >= 2) return false;
        const status = error instanceof ApiError ? error.status : undefined;
        // 4xx means the request itself is wrong — retrying cannot help.
        if (status !== undefined && status >= 400 && status < 500 && status !== 429) return false;
        // Timeouts and 5xx/429: retry with the QueryClient's own backoff.
        return error instanceof ApiTimeoutError || (status !== undefined && (status >= 500 || status === 429));
      },
    },
  },
});

/** Invalidate every query under a project key prefix (used by realtime). */
export function invalidateProject(prefix: string, projectId: string) {
  void queryClient.invalidateQueries({ queryKey: [prefix, projectId] });
}
