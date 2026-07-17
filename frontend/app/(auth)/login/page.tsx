"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { LOGIN_URL } from "@/lib/auth/api";
import { useSession } from "@/lib/auth/use-auth";

// The inner component reads search params, so it must live under a Suspense
// boundary (Next.js 15 requirement for statically-rendered pages).
function LoginContent() {
  const router = useRouter();
  const params = useSearchParams();
  const { isAuthenticated, isLoading } = useSession();
  const error = params.get("error");

  // Already signed in? Skip the login screen.
  useEffect(() => {
    if (!isLoading && isAuthenticated) router.replace("/dashboard");
  }, [isLoading, isAuthenticated, router]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8 text-center">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Welcome to MatchSync</h1>
        <p className="text-muted-foreground">
          Sign in to sync sports fixtures into your Google Calendar.
        </p>
      </div>

      {error && (
        <p className="rounded-md bg-destructive/10 px-4 py-2 text-sm text-destructive">
          Sign-in failed. Please try again.
        </p>
      )}

      <Button asChild size="lg">
        {/* Full navigation (not fetch) so the browser follows the OAuth redirect. */}
        <a href={LOGIN_URL}>Sign in with Google</a>
      </Button>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginContent />
    </Suspense>
  );
}
