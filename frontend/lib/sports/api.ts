/** Sports metadata API calls + types. Thin wrappers over the shared API client. */

import { apiFetch } from "@/lib/api/client";

export interface SportDto {
  key: string;
  name: string;
  category: string;
  provider_key: string;
}

export interface SeasonDto {
  label: string;
  start: string | null;
  end: string | null;
  is_current: boolean;
}

export interface CompetitionDto {
  external_id: string;
  name: string;
  sport_key: string;
  type: string;
  country: string | null;
  season: SeasonDto | null;
  logo_url: string | null;
}

export interface TeamDto {
  external_id: string;
  name: string;
  sport_key: string;
  short_name: string | null;
  country: string | null;
  logo_url: string | null;
}

export interface ProviderInfoDto {
  key: string;
  name: string;
  version: string;
  capabilities: string[];
  supported_sports: string[];
  configured: boolean;
}

export interface SearchHitDto {
  type: "sport" | "competition" | "team" | "tournament";
  id: string;
  name: string;
  sport_key: string | null;
  subtitle: string | null;
  logo_url: string | null;
}

export interface SearchResultsDto {
  query: string;
  total: number;
  hits: SearchHitDto[];
}

export interface ProviderRefreshReportDto {
  provider_key: string;
  success: boolean;
  sports: number;
  competitions: number;
  teams: number;
  errors: string[];
}

export interface MetadataRefreshReportDto {
  ok: boolean;
  providers: ProviderRefreshReportDto[];
}

export function fetchSports(): Promise<SportDto[]> {
  return apiFetch<SportDto[]>("/sports");
}

export function fetchCompetitions(sportKey: string): Promise<CompetitionDto[]> {
  return apiFetch<CompetitionDto[]>(`/competitions?sport=${encodeURIComponent(sportKey)}`);
}

export function fetchTeams(sportKey: string, competitionId: string): Promise<TeamDto[]> {
  const params = new URLSearchParams({ sport: sportKey, competition: competitionId });
  return apiFetch<TeamDto[]>(`/teams?${params}`);
}

export function fetchProviders(): Promise<ProviderInfoDto[]> {
  return apiFetch<ProviderInfoDto[]>("/providers");
}

export function fetchCapabilities(): Promise<Record<string, string[]>> {
  return apiFetch<Record<string, string[]>>("/capabilities");
}

export function searchCatalog(query: string): Promise<SearchResultsDto> {
  return apiFetch<SearchResultsDto>(`/search?q=${encodeURIComponent(query)}`);
}

export function refreshMetadata(): Promise<MetadataRefreshReportDto> {
  return apiFetch<MetadataRefreshReportDto>("/metadata/refresh", { method: "POST" });
}
