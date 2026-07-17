import { Button } from "@/components/ui/button";

// Placeholder landing page. Proves Tailwind + shadcn render correctly. Real
// marketing/dashboard routes arrive with their features in later stages.
export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8 text-center">
      <div className="space-y-3">
        <h1 className="text-4xl font-bold tracking-tight">MatchSync</h1>
        <p className="max-w-md text-muted-foreground">
          Automatically synchronize sports fixtures into your Google Calendar.
        </p>
      </div>
      <div className="flex gap-3">
        <Button asChild>
          <a href="/login">Get started</a>
        </Button>
        <Button variant="outline" asChild>
          <a href="/login">Sign in</a>
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Foundation build · v0.1.0
      </p>
    </main>
  );
}
