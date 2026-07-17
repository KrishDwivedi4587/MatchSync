/** Fixture ingestion + browsing API calls and types. */

import { apiFetch } from "@/lib/api/client";

export interface TeamRef {
  id: string;
  name: string;
  short_name: string | null;
  logo_url: string | null;
}

export interface FixtureDto {
  id: string;
  competition_id: string;
  competition_name: string | null;
  provider_fixture_id: string;
  identity_key: string;
  scheduled_start: string;
  scheduled_end: string | null;
  status: string;
  venue: string | null;
  round: string | null;
  stage: string | null;
  version: number;
  home_team: TeamRef | null;
  away_team: TeamRef | null;
}

export interface FixtureVersionDto {
  version: number;
  change_type: string;
  changed_fields: string[];
  content_hash: string;
  created_at: string;
  import_run_id: string | null;
}

export interface FixtureDetailDto extends FixtureDto {
  versions: FixtureVersionDto[];
}

export interface FixtureListResponse {
  total: number;
  limit: number;
  offset: number;
  fixtures: FixtureDto[];
}

export interface ImportStats {
  fetched: number;
  invalid: number;
  duplicates: number;
  created: number;
  updated: number;
  unchanged: number;
  skipped_out_of_window: number;
  skipped_stale: number;
  missing_marked: number;
  deleted: number;
  failed: number;
}

export interface ImportIssue {
  code: string;
  message: string;
  severity: string;
  external_id: string | null;
  competition_id: string | null;
}

export interface ImportReportDto {
  id: string;
  provider_key: string;
  sport_key: string | null;
  status: string;
  duration_ms: number;
  started_at: string | null;
  finished_at: string | null;
  stats: ImportStats;
  errors: ImportIssue[];
  warnings: ImportIssue[];
}

export interface ImportRunSummary {
  id: string;
  provider_key: string;
  sport_key: string | null;
  status: string;
  duration_ms: number;
  created_at: string;
  finished_at: string | null;
  fetched_count: number;
  created_count: number;
  updated_count: number;
  unchanged_count: number;
  skipped_count: number;
  duplicate_count: number;
  invalid_count: number;
  failed_count: number;
  deleted_count: number;
  error_summary: string | null;
}

export interface FixtureFilters {
  sport?: string;
  status?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export function fetchFixtures(filters: FixtureFilters): Promise<FixtureListResponse> {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return apiFetch<FixtureListResponse>(`/fixtures?${params}`);
}

export function fetchFixture(id: string): Promise<FixtureDetailDto> {
  return apiFetch<FixtureDetailDto>(`/fixtures/${id}`);
}

export function importFixtures(sport: string): Promise<ImportReportDto> {
  return apiFetch<ImportReportDto>("/fixtures/import", {
    method: "POST",
    body: JSON.stringify({ sport }),
  });
}

export function importProvider(provider: string): Promise<ImportReportDto> {
  return apiFetch<ImportReportDto>("/fixtures/import/provider", {
    method: "POST",
    body: JSON.stringify({ provider }),
  });
}

export function fetchImportRuns(): Promise<{ runs: ImportRunSummary[] }> {
  return apiFetch<{ runs: ImportRunSummary[] }>("/fixtures/import/status");
}
