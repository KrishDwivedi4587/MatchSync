"use client";

/** Synchronization hooks. Server state lives in TanStack Query. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type SyncMode,
  fetchSyncHistory,
  fetchSyncMetrics,
  fetchSyncPlan,
  fetchSyncReport,
  fetchSyncStatus,
  runSync,
} from "@/lib/sync/api";

const SYNC_KEY = ["sync"] as const;

export function useSyncStatus() {
  return useQuery({
    queryKey: [...SYNC_KEY, "status"],
    queryFn: fetchSyncStatus,
    staleTime: 15_000,
  });
}

export function useSyncHistory() {
  return useQuery({
    queryKey: [...SYNC_KEY, "history"],
    queryFn: () => fetchSyncHistory(),
    staleTime: 15_000,
  });
}

export function useSyncMetrics() {
  return useQuery({
    queryKey: [...SYNC_KEY, "metrics"],
    queryFn: fetchSyncMetrics,
    staleTime: 30_000,
  });
}

export function useSyncReport(runId: string | null) {
  return useQuery({
    queryKey: [...SYNC_KEY, "report", runId ?? ""],
    queryFn: () => fetchSyncReport(runId!),
    enabled: Boolean(runId),
  });
}

/** Plan preview: safe to call freely — the backend performs no writes. */
export function useSyncPlan(subscriptionId: string | null, mode: SyncMode) {
  return useQuery({
    queryKey: [...SYNC_KEY, "plan", subscriptionId ?? "", mode],
    queryFn: () => fetchSyncPlan(subscriptionId!, mode),
    enabled: Boolean(subscriptionId),
    staleTime: 0,
  });
}

export function useRunSync() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ subscriptionId, mode }: { subscriptionId: string | null; mode: SyncMode }) =>
      runSync(subscriptionId, mode),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SYNC_KEY });
      void queryClient.invalidateQueries({ queryKey: ["fixtures"] });
    },
  });
}
