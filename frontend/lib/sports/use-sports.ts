"use client";

/** Sports metadata hooks. Server state lives in TanStack Query (Stage 1's rule). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { queryKeys } from "@/lib/query/keys";
import {
  fetchCompetitions,
  fetchProviders,
  fetchSports,
  fetchTeams,
  refreshMetadata,
  searchCatalog,
} from "@/lib/sports/api";

const METADATA_STALE_TIME = 5 * 60_000; // reference data changes rarely

export function useSports() {
  return useQuery({
    queryKey: queryKeys.sports,
    queryFn: fetchSports,
    staleTime: METADATA_STALE_TIME,
  });
}

export function useCompetitions(sportKey: string | null) {
  return useQuery({
    queryKey: queryKeys.competitions(sportKey ?? ""),
    queryFn: () => fetchCompetitions(sportKey!),
    enabled: Boolean(sportKey),
    staleTime: METADATA_STALE_TIME,
  });
}

export function useTeams(sportKey: string | null, competitionId: string | null) {
  return useQuery({
    queryKey: queryKeys.teams(sportKey ?? "", competitionId ?? ""),
    queryFn: () => fetchTeams(sportKey!, competitionId!),
    enabled: Boolean(sportKey && competitionId),
    staleTime: METADATA_STALE_TIME,
  });
}

export function useProviders() {
  return useQuery({
    queryKey: queryKeys.providers,
    queryFn: fetchProviders,
    staleTime: METADATA_STALE_TIME,
  });
}

export function useCatalogSearch(query: string) {
  const term = query.trim();
  return useQuery({
    queryKey: queryKeys.sportsSearch(term),
    queryFn: () => searchCatalog(term),
    enabled: term.length > 1,
    staleTime: 30_000,
  });
}

export function useRefreshMetadata() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: refreshMetadata,
    onSuccess: () => {
      // The whole sports namespace is stale after a refresh.
      void queryClient.invalidateQueries({ queryKey: queryKeys.sports });
    },
  });
}
