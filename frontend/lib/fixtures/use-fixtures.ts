"use client";

/** Fixture hooks. Server state lives in TanStack Query (Stage 1's rule). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type FixtureFilters,
  fetchFixture,
  fetchFixtures,
  fetchImportRuns,
  importFixtures,
  importProvider,
} from "@/lib/fixtures/api";
import { queryKeys } from "@/lib/query/keys";

export function useFixtures(filters: FixtureFilters) {
  return useQuery({
    queryKey: queryKeys.fixtures(filters as Record<string, unknown>),
    queryFn: () => fetchFixtures(filters),
    staleTime: 30_000,
  });
}

export function useFixture(id: string | null) {
  return useQuery({
    queryKey: queryKeys.fixture(id ?? ""),
    queryFn: () => fetchFixture(id!),
    enabled: Boolean(id),
  });
}

export function useImportRuns() {
  return useQuery({
    queryKey: queryKeys.importRuns,
    queryFn: fetchImportRuns,
    staleTime: 15_000,
  });
}

function useInvalidateAfterImport() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["fixtures"] });
  };
}

export function useImportFixtures() {
  const invalidate = useInvalidateAfterImport();
  return useMutation({
    mutationFn: (sport: string) => importFixtures(sport),
    onSuccess: invalidate,
  });
}

export function useImportProvider() {
  const invalidate = useInvalidateAfterImport();
  return useMutation({
    mutationFn: (provider: string) => importProvider(provider),
    onSuccess: invalidate,
  });
}
