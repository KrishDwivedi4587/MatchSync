"use client";

/** Application-layer hooks. Server state via TanStack Query (Stage 1's rule). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type CreateSubscriptionBody,
  bulkSubscribe,
  createSubscription,
  deleteSubscription,
  fetchDashboard,
  fetchOnboarding,
  fetchPreferences,
  fetchSubscriptions,
  pauseSubscription,
  resumeSubscription,
  savePreferences,
  updateProfile,
  updateSubscription,
  type Preferences,
} from "@/lib/app/api";

const KEYS = {
  subscriptions: ["subscriptions"] as const,
  dashboard: ["dashboard"] as const,
  onboarding: ["onboarding"] as const,
  preferences: ["preferences"] as const,
};

export function useSubscriptions() {
  return useQuery({ queryKey: KEYS.subscriptions, queryFn: fetchSubscriptions, staleTime: 30_000 });
}

export function useDashboard() {
  return useQuery({
    queryKey: KEYS.dashboard,
    queryFn: fetchDashboard,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export function useOnboarding() {
  return useQuery({ queryKey: KEYS.onboarding, queryFn: fetchOnboarding, staleTime: 5_000 });
}

export function usePreferences() {
  return useQuery({ queryKey: KEYS.preferences, queryFn: fetchPreferences, staleTime: 60_000 });
}

function useInvalidateAll() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: KEYS.subscriptions });
    void qc.invalidateQueries({ queryKey: KEYS.dashboard });
    void qc.invalidateQueries({ queryKey: KEYS.onboarding });
  };
}

export function useCreateSubscription() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (body: CreateSubscriptionBody) => createSubscription(body),
    onSuccess: invalidate,
  });
}

export function useBulkSubscribe() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (items: CreateSubscriptionBody[]) => bulkSubscribe(items),
    onSuccess: invalidate,
  });
}

export function useUpdateSubscription() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (args: { id: string; body: Parameters<typeof updateSubscription>[1] }) =>
      updateSubscription(args.id, args.body),
    onSuccess: invalidate,
  });
}

export function useDeleteSubscription() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: deleteSubscription, onSuccess: invalidate });
}

export function usePauseSubscription() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: pauseSubscription, onSuccess: invalidate });
}

export function useResumeSubscription() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: resumeSubscription, onSuccess: invalidate });
}

export function useSavePreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (prefs: Preferences) => savePreferences(prefs),
    onSuccess: () => void qc.invalidateQueries({ queryKey: KEYS.preferences }),
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: updateProfile,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["auth", "status"] }),
  });
}
