"use client";

/**
 * Authentication hooks.
 *
 * Session/user is SERVER state -> owned by TanStack Query (Stage 1's hard rule),
 * not mirrored into Zustand. `useSession` restores the session on load by
 * querying /auth/status; `useLogout` revokes it and resets the cache.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { fetchAuthStatus, logoutRequest } from "@/lib/auth/api";
import { queryKeys } from "@/lib/query/keys";

export function useSession() {
  const query = useQuery({
    queryKey: queryKeys.authStatus,
    queryFn: fetchAuthStatus,
    staleTime: 60_000,
    retry: false,
  });

  return {
    user: query.data?.user ?? null,
    isAuthenticated: query.data?.authenticated ?? false,
    isLoading: query.isLoading,
    isError: query.isError,
  };
}

export function useLogout() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: logoutRequest,
    onSuccess: () => {
      queryClient.setQueryData(queryKeys.authStatus, {
        authenticated: false,
        user: null,
      });
      queryClient.clear();
      router.push("/login");
    },
  });
}
