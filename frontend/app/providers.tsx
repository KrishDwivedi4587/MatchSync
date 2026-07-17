"use client";

/** Client-side provider tree. Wraps the app in the TanStack Query context. */

import type { ReactNode } from "react";

import { Toaster } from "@/components/ui/toaster";
import { QueryProvider } from "@/lib/query/provider";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      {children}
      <Toaster />
    </QueryProvider>
  );
}
