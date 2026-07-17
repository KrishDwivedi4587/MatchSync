/** Orchestration API calls and types. */

import { apiFetch } from "@/lib/api/client";

export interface JobDto {
  id: string;
  type: string;
  state: string;
  priority: number;
  queue: string;
  payload: Record<string, unknown>;
  attempts: number;
  max_attempts: number;
  error: string | null;
  error_code: string | null;
  created_at: string;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  next_retry_at: string | null;
  queue_latency_seconds: number | null;
  duration_seconds: number | null;
}

export interface JobListResponse {
  jobs: JobDto[];
  total: number;
}

export interface WorkersResponse {
  workers: { name: string; seen_at: string; state?: string }[];
  online: number;
}

export interface QueueResponse {
  depths: Record<string, number>;
  total: number;
}

export interface SchedulerStatus {
  alive: boolean;
  last_seen_at: string | null;
  jobs: {
    key: string;
    name: string;
    schedule: string;
    status: string;
    last_run_at: string | null;
    next_run_at: string | null;
  }[];
}

export interface OrchestrationHealth {
  healthy: boolean;
  redis: boolean;
  workers_online: number;
  scheduler_alive: boolean;
  stuck_jobs: number;
}

export const JOB_STATES = [
  "",
  "queued",
  "running",
  "retrying",
  "succeeded",
  "skipped",
  "failed",
  "dead_letter",
  "cancelled",
];

export function fetchJobs(state?: string, limit = 50): Promise<JobListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (state) params.set("state", state);
  return apiFetch(`/jobs?${params}`);
}

export function fetchDeadLetter(): Promise<JobListResponse> {
  return apiFetch("/jobs/dead-letter");
}

export function fetchWorkers(): Promise<WorkersResponse> {
  return apiFetch("/workers");
}

export function fetchQueue(): Promise<QueueResponse> {
  return apiFetch("/queue");
}

export function fetchSchedulerStatus(): Promise<SchedulerStatus> {
  return apiFetch("/scheduler/status");
}

export function fetchOrchestrationHealth(): Promise<OrchestrationHealth> {
  return apiFetch("/orchestration/health");
}

export function fetchOrchestrationMetrics(): Promise<{ metrics: Record<string, unknown> }> {
  return apiFetch("/orchestration/metrics");
}

export function enqueueSync(subscriptionId: string | null): Promise<JobDto> {
  return apiFetch("/jobs/sync", {
    method: "POST",
    body: JSON.stringify({ subscription_id: subscriptionId }),
  });
}

export function retryJob(jobId: string): Promise<JobDto> {
  return apiFetch(`/jobs/${jobId}/retry`, { method: "POST" });
}

export function cancelJob(jobId: string): Promise<JobDto> {
  return apiFetch(`/jobs/${jobId}/cancel`, { method: "POST" });
}
