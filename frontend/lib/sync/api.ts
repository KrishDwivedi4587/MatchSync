/** Synchronization API calls and types. */

import { apiFetch } from "@/lib/api/client";

export type SyncMode = "incremental" | "full" | "reconcile";

export interface PlanStats {
  create: number;
  recreate: number;
  update: number;
  cancel: number;
  delete: number;
  reconcile: number;
  conflict: number;
  no_op: number;
  total: number;
  mutations: number;
  no_op_ratio: number;
}

export interface SyncActionDto {
  type: string;
  identity_key: string;
  reason: string;
  fixture_id: string | null;
  external_event_id: string | null;
  changed_fields: string[];
}

export interface SyncPlanDto {
  subscription_id: string;
  mode: SyncMode;
  is_empty: boolean;
  stats: PlanStats;
  actions: SyncActionDto[];
}

export interface SyncReportDto {
  run_id: string | null;
  subscription_id: string;
  mode: SyncMode;
  status: string;
  plan: PlanStats;
  created: number;
  updated: number;
  deleted: number;
  skipped: number;
  failed: number;
  duplicates_prevented: number;
  api_calls: number;
  plan_ms: number;
  execute_ms: number;
  total_ms: number;
  error_summary: string | null;
}

export interface SyncRunDto {
  id: string;
  subscription_id: string;
  trigger: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  created_count: number;
  updated_count: number;
  deleted_count: number;
  skipped_count: number;
  failed_count: number;
  error_summary: string | null;
}

export interface SyncOperationDto {
  operation_type: string;
  status: string;
  fixture_id: string | null;
  message: string | null;
  created_at: string;
}

export interface SyncRunDetailDto extends SyncRunDto {
  operations: SyncOperationDto[];
}

export interface SubscriptionStatusDto {
  subscription_id: string;
  status: string;
  sync_frequency_minutes: number;
  last_synced_at: string | null;
  next_sync_at: string | null;
  last_run: SyncRunDto | null;
}

export function fetchSyncStatus(): Promise<{ subscriptions: SubscriptionStatusDto[] }> {
  return apiFetch("/sync/status");
}

export function fetchSyncHistory(limit = 20): Promise<{ runs: SyncRunDto[] }> {
  return apiFetch(`/sync/history?limit=${limit}`);
}

export function fetchSyncReport(runId: string): Promise<SyncRunDetailDto> {
  return apiFetch(`/sync/report/${runId}`);
}

export function fetchSyncMetrics(): Promise<{ metrics: Record<string, number | object> }> {
  return apiFetch("/sync/metrics");
}

export function fetchSyncPlan(subscriptionId: string, mode: SyncMode): Promise<SyncPlanDto> {
  return apiFetch(`/sync/plan?subscription_id=${subscriptionId}&mode=${mode}`);
}

export function runSync(
  subscriptionId: string | null,
  mode: SyncMode,
): Promise<{ reports: SyncReportDto[] }> {
  return apiFetch("/sync", {
    method: "POST",
    body: JSON.stringify({ subscription_id: subscriptionId, mode }),
  });
}
