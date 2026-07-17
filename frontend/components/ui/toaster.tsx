"use client";

import { CheckCircle2, Info, X, XCircle } from "lucide-react";

import { useToastStore } from "@/stores/toast-store";
import { cn } from "@/lib/utils";

const ICON = {
  success: <CheckCircle2 className="h-4 w-4 text-primary" aria-hidden />,
  error: <XCircle className="h-4 w-4 text-destructive" aria-hidden />,
  info: <Info className="h-4 w-4 text-muted-foreground" aria-hidden />,
};

/** Accessible toast region. Mounted once in the provider tree. */
export function Toaster() {
  const { toasts, dismiss } = useToastStore();

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
      role="region"
      aria-label="Notifications"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className={cn(
            "pointer-events-auto flex items-start gap-3 rounded-lg border bg-background p-3 text-sm shadow-lg",
            t.variant === "error" && "border-destructive/40",
          )}
        >
          {ICON[t.variant]}
          <p className="flex-1">{t.message}</p>
          <button
            type="button"
            onClick={() => dismiss(t.id)}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Dismiss notification"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
      ))}
    </div>
  );
}
