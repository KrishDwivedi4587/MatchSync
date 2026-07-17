/**
 * Typed API client for our own backend.
 *
 * - Same-origin base path (`/api/v1`) served through the Next.js proxy so the
 *   httpOnly auth cookies are sent automatically as first-party cookies.
 * - `credentials: "include"` always attaches cookies.
 * - Transparent 401 handling: on an expired access token it calls the refresh
 *   endpoint once (single-flight) and replays the original request.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown; request_id?: string };
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Endpoints that must never trigger the refresh-and-retry loop.
const NO_RETRY = ["/auth/refresh", "/auth/login", "/auth/callback"];

let refreshInFlight: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  // Single-flight: concurrent 401s share one refresh call.
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${API_BASE_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

async function rawFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res = await rawFetch(path, init);

  if (res.status === 401 && !NO_RETRY.some((p) => path.startsWith(p))) {
    const refreshed = await refreshSession();
    if (refreshed) res = await rawFetch(path, init);
  }

  if (!res.ok) {
    let body: ApiErrorBody | undefined;
    try {
      body = (await res.json()) as ApiErrorBody;
    } catch {
      /* non-JSON error body */
    }
    // `?.` on every level: a proxy or infrastructure layer can return JSON that
    // is not our envelope (e.g. {"detail": ...}); `body?.error.code` would then
    // throw inside the error path itself.
    throw new ApiError(
      res.status,
      body?.error?.code ?? "unknown_error",
      body?.error?.message ?? res.statusText,
      body?.error?.request_id,
    );
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
