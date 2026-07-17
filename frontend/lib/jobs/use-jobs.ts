"use client";

/**
 * Orchestration hooks. Live updates come from short-interval polling rather
 * than websockets: the dashboard is low-traffic, and polling survives worker
 * restarts, proxies, and scale-out without any sticky-session infrastructure.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelJob,
  enqueueSync,
  fetchDeadLetter,
  fetchJobs,
  fetchOrchestrationHealth,
  fetchOrchestrationMetrics,
  fetchQueue,
  fetchSchedulerStatus,
  fetchWorkers,
  retryJob,
} from "@/lib/jobs/api";

const KEY = ["orchestration"] as const;
const LIVE_INTERVAL = 5_000;

export function useJobs(state: string) {
  return useQuery({
    queryKey: [...KEY, "jobs", state],
    queryFn: () => fetchJobs(state || undefined),
    refetchInterval: LIVE_INTERVAL,
  });
}

export function useDeadLetter() {
  return useQuery({
    queryKey: [...KEY, "dead-letter"],
    queryFn: fetchDeadLetter,
    refetchInterval: LIVE_INTERVAL * 4,
  });
}

export function useWorkers() {
  return useQuery({
    queryKey: [...KEY, "workers"],
    queryFn: fetchWorkers,
    refetchInterval: LIVE_INTERVAL,
  });
}

export function useQueueDepth() {
  return useQuery({
    queryKey: [...KEY, "queue"],
    queryFn: fetchQueue,
    refetchInterval: LIVE_INTERVAL,
  });
}

export function useSchedulerStatus() {
  return useQuery({
    queryKey: [...KEY, "scheduler"],
    queryFn: fetchSchedulerStatus,
    refetchInterval: LIVE_INTERVAL * 2,
  });
}

export function useOrchestrationHealth() {
  return useQuery({
    queryKey: [...KEY, "health"],
    queryFn: fetchOrchestrationHealth,
    refetchInterval: LIVE_INTERVAL,
  });
}

export function useOrchestrationMetrics() {
  return useQuery({
    queryKey: [...KEY, "metrics"],
    queryFn: fetchOrchestrationMetrics,
    refetchInterval: LIVE_INTERVAL * 2,
  });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: KEY });
}

export function useEnqueueSync() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (subscriptionId: string | null) => enqueueSync(subscriptionId),
    onSuccess: invalidate,
  });
}

export function useRetryJob() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: retryJob, onSuccess: invalidate });
}

export function useCancelJob() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: cancelJob, onSuccess: invalidate });
}
