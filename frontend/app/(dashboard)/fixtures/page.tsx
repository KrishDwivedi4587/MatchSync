"use client";

import { useState } from "react";
import { Download, Search } from "lucide-react";

import { ImportReportView, ImportRunsTable } from "@/components/features/import-report";
import { Button } from "@/components/ui/button";
import { useFixture, useFixtures, useImportFixtures, useImportRuns } from "@/lib/fixtures/use-fixtures";
import { useSports } from "@/lib/sports/use-sports";
import { cn } from "@/lib/utils";

const STATUSES = ["", "scheduled", "live", "finished", "postponed", "cancelled"];
const PAGE_SIZE = 20;

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

// Fixture browser + import page + report viewer. No synchronization controls —
// nothing here compares fixtures against a calendar. That is Stage 8.
export default function FixturesPage() {
  const [sport, setSport] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const sports = useSports();
  const runs = useImportRuns();
  const runImport = useImportFixtures();
  const fixtures = useFixtures({
    sport: sport || undefined,
    status: status || undefined,
    q: q.trim() || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const detail = useFixture(selectedId);

  const total = fixtures.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">Fixtures</h1>
          <p className="text-sm text-muted-foreground">
            Import and browse fixtures ingested from sports providers.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => sport && runImport.mutate(sport)}
          disabled={!sport || runImport.isPending}
          title={sport ? undefined : "Select a sport to import"}
        >
          <Download className={cn("h-4 w-4", runImport.isPending && "animate-pulse")} aria-hidden />
          {runImport.isPending ? "Importing…" : "Import fixtures"}
        </Button>
      </header>

      {runImport.isError && (
        <p className="text-sm text-destructive">Import failed. Check the provider status.</p>
      )}
      {runImport.data && <ImportReportView report={runImport.data} />}

      {/* Filters */}
      <section className="grid gap-3 sm:grid-cols-3">
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">Sport</span>
          <select
            value={sport}
            onChange={(e) => {
              setSport(e.target.value);
              setPage(0);
            }}
            className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="">All sports</option>
            {sports.data?.map((s) => (
              <option key={s.key} value={s.key}>
                {s.name}
              </option>
            ))}
          </select>
        </label>

        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">Status</span>
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(0);
            }}
            className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm capitalize"
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s === "" ? "All statuses" : s}
              </option>
            ))}
          </select>
        </label>

        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">Search</span>
          <div className="relative">
            <Search
              className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(0);
              }}
              placeholder="Team or competition…"
              className="h-10 w-full rounded-md border border-input bg-background pl-9 pr-3 text-sm"
            />
          </div>
        </label>
      </section>

      {/* Fixture list */}
      <section className="space-y-3">
        {fixtures.isLoading ? (
          <div className="space-y-2" aria-busy>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-16 animate-pulse rounded-md bg-muted" />
            ))}
          </div>
        ) : fixtures.isError ? (
          <p className="text-sm text-destructive">Could not load fixtures.</p>
        ) : total === 0 ? (
          <p className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
            No fixtures found. Import some first.
          </p>
        ) : (
          <>
            <ul className="divide-y rounded-lg border">
              {fixtures.data?.fixtures.map((fixture) => (
                <li key={fixture.id}>
                  <button
                    type="button"
                    onClick={() =>
                      setSelectedId(selectedId === fixture.id ? null : fixture.id)
                    }
                    className="flex w-full flex-wrap items-center justify-between gap-3 p-4 text-left hover:bg-accent/50"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium">
                        {fixture.home_team?.name ?? "TBD"} vs {fixture.away_team?.name ?? "TBD"}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {fixture.competition_name} · {formatKickoff(fixture.scheduled_start)}
                        {fixture.venue ? ` · ${fixture.venue}` : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-xs">
                      <span className="rounded bg-muted px-1.5 py-0.5 capitalize">
                        {fixture.status}
                      </span>
                      <span className="text-muted-foreground">v{fixture.version}</span>
                    </div>
                  </button>

                  {selectedId === fixture.id && detail.data && (
                    <div className="border-t bg-muted/30 p-4">
                      <p className="mb-2 text-xs font-medium uppercase text-muted-foreground">
                        Version history
                      </p>
                      <ul className="space-y-1 text-xs">
                        {detail.data.versions.map((version) => (
                          <li key={version.version} className="flex flex-wrap gap-2">
                            <span className="font-mono">v{version.version}</span>
                            <span className="font-medium capitalize">{version.change_type}</span>
                            {version.changed_fields.length > 0 && (
                              <span className="text-muted-foreground">
                                ({version.changed_fields.join(", ")})
                              </span>
                            )}
                            <span className="text-muted-foreground">
                              {formatKickoff(version.created_at)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </li>
              ))}
            </ul>

            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                {total} fixture{total === 1 ? "" : "s"} · page {page + 1} of {pages}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page + 1 >= pages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </section>

      {/* Import history */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Recent imports</h2>
        {runs.isLoading ? (
          <div className="h-24 animate-pulse rounded-md bg-muted" aria-busy />
        ) : (
          <ImportRunsTable runs={runs.data?.runs ?? []} />
        )}
      </section>
    </div>
  );
}
