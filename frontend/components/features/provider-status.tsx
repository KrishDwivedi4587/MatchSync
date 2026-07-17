"use client";

import { AlertTriangle, CheckCircle2 } from "lucide-react";

import type { ProviderInfoDto } from "@/lib/sports/api";

/** Provider status + capability display. Read-only; no sync controls. */
export function ProviderStatusList({ providers }: { providers: ProviderInfoDto[] }) {
  if (providers.length === 0) {
    return <p className="text-sm text-muted-foreground">No providers registered.</p>;
  }

  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {providers.map((provider) => (
        <li key={provider.key} className="rounded-lg border p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate font-medium">{provider.name}</p>
              <p className="text-xs text-muted-foreground">
                {provider.key} · {provider.version}
              </p>
            </div>
            {provider.configured ? (
              <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                <CheckCircle2 className="h-4 w-4 text-primary" aria-hidden />
                Configured
              </span>
            ) : (
              <span className="flex shrink-0 items-center gap-1 text-xs text-destructive">
                <AlertTriangle className="h-4 w-4" aria-hidden />
                No API key
              </span>
            )}
          </div>

          <div className="mt-3 flex flex-wrap gap-1">
            {provider.capabilities.length === 0 ? (
              <span className="text-xs text-muted-foreground">No extra capabilities</span>
            ) : (
              provider.capabilities.map((capability) => (
                <span
                  key={capability}
                  className="rounded bg-secondary px-1.5 py-0.5 text-xs text-secondary-foreground"
                >
                  {capability.replace(/_/g, " ")}
                </span>
              ))
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
