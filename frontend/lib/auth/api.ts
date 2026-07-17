/** Auth API calls + types. Thin wrappers over the shared API client. */

import { apiFetch } from "@/lib/api/client";

export interface AuthUser {
  id: string;
  email: string;
  display_name: string | null;
  timezone: string;
  status: string;
  created_at: string;
}

export interface AuthStatus {
  authenticated: boolean;
  user: AuthUser | null;
}

/** Full-page navigation target that begins the Google OAuth flow. */
export const LOGIN_URL = "/api/v1/auth/login";

export function fetchAuthStatus(): Promise<AuthStatus> {
  return apiFetch<AuthStatus>("/auth/status");
}

export function logoutRequest(): Promise<void> {
  return apiFetch<void>("/auth/logout", { method: "POST" });
}
