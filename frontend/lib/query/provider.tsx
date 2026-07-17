"use client";

/**
 * TanStack Query provider.
 *
 * Server state lives here (Stage 1's hard rule: server state -> Query, client
 * state -> Zustand). The QueryClient is created once per browser session inside
 * state so it is never shared across requests on the server.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000, // sensible default; per-query overrides later
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
