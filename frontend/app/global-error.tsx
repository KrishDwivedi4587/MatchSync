"use client";

// Last-resort boundary that replaces the root layout when it itself throws.
export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <html lang="en">
      <body className="flex min-h-screen items-center justify-center bg-background text-foreground">
        <div className="space-y-4 text-center">
          <h2 className="text-lg font-semibold">MatchSync hit an unexpected error</h2>
          <button
            type="button"
            onClick={reset}
            className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
