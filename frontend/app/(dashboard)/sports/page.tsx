"use client";

import { useState } from "react";
import { RefreshCw, Search } from "lucide-react";

import { ProviderStatusList } from "@/components/features/provider-status";
import { Button } from "@/components/ui/button";
import {
  useCatalogSearch,
  useCompetitions,
  useProviders,
  useRefreshMetadata,
  useSports,
  useTeams,
} from "@/lib/sports/use-sports";
import { cn } from "@/lib/utils";

function Skeletons({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-12 animate-pulse rounded-md bg-muted" />
      ))}
    </div>
  );
}

// Sports metadata browser: sports -> competitions -> teams, plus catalog
// search, provider status, capability display, and a manual metadata refresh.
// No fixtures and no sync controls — those arrive in Stage 7.
export default function SportsPage() {
  const [sportKey, setSportKey] = useState<string | null>(null);
  const [competitionId, setCompetitionId] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const sports = useSports();
  const competitions = useCompetitions(sportKey);
  const teams = useTeams(sportKey, competitionId);
  const providers = useProviders();
  const search = useCatalogSearch(query);
  const refresh = useRefreshMetadata();

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">Sports catalog</h1>
          <p className="text-sm text-muted-foreground">
            Browse sports, competitions, and teams from every registered provider.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
        >
          <RefreshCw
            className={cn("h-4 w-4", refresh.isPending && "animate-spin")}
            aria-hidden
          />
          {refresh.isPending ? "Refreshing…" : "Refresh metadata"}
        </Button>
      </header>

      {refresh.isSuccess && (
        <p className="rounded-md bg-secondary px-3 py-2 text-sm">
          Refreshed{" "}
          {refresh.data.providers.reduce((n, p) => n + p.competitions, 0)} competitions and{" "}
          {refresh.data.providers.reduce((n, p) => n + p.teams, 0)} teams.
        </p>
      )}
      {refresh.isError && (
        <p className="text-sm text-destructive">Metadata refresh failed. Try again.</p>
      )}

      {/* Search */}
      <section className="space-y-3">
        <label htmlFor="catalog-search" className="text-lg font-semibold">
          Search
        </label>
        <div className="relative">
          <Search
            className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <input
            id="catalog-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search competitions and teams…"
            className="h-10 w-full rounded-md border border-input bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>

        {search.isFetching && <p className="text-sm text-muted-foreground">Searching…</p>}
        {search.data && search.data.hits.length > 0 && (
          <ul className="divide-y rounded-lg border">
            {search.data.hits.map((hit) => (
              <li key={`${hit.type}-${hit.id}`} className="flex items-center gap-3 p-3">
                <span className="rounded bg-muted px-1.5 py-0.5 text-xs capitalize">
                  {hit.type}
                </span>
                <span className="truncate text-sm font-medium">{hit.name}</span>
                {hit.subtitle && (
                  <span className="truncate text-xs text-muted-foreground">{hit.subtitle}</span>
                )}
              </li>
            ))}
          </ul>
        )}
        {search.data && search.data.hits.length === 0 && query.trim().length > 1 && (
          <p className="text-sm text-muted-foreground">
            No matches. Try refreshing metadata first.
          </p>
        )}
      </section>

      {/* Browser: sports -> competitions -> teams */}
      <section className="grid gap-6 md:grid-cols-3">
        <div className="space-y-2">
          <h2 className="text-lg font-semibold">Sports</h2>
          {sports.isLoading ? (
            <Skeletons />
          ) : sports.isError ? (
            <p className="text-sm text-destructive">Could not load sports.</p>
          ) : (
            <ul className="space-y-1">
              {sports.data?.map((sport) => (
                <li key={sport.key}>
                  <button
                    type="button"
                    onClick={() => {
                      setSportKey(sport.key);
                      setCompetitionId(null);
                    }}
                    className={cn(
                      "w-full rounded-md border p-3 text-left text-sm transition-colors hover:bg-accent/50",
                      sportKey === sport.key && "border-primary bg-accent",
                    )}
                  >
                    <span className="font-medium">{sport.name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {sport.provider_key}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-2">
          <h2 className="text-lg font-semibold">Competitions</h2>
          {!sportKey ? (
            <p className="text-sm text-muted-foreground">Select a sport.</p>
          ) : competitions.isLoading ? (
            <Skeletons />
          ) : competitions.isError ? (
            <p className="text-sm text-destructive">Could not load competitions.</p>
          ) : (
            <ul className="space-y-1">
              {competitions.data?.map((competition) => (
                <li key={competition.external_id}>
                  <button
                    type="button"
                    onClick={() => setCompetitionId(competition.external_id)}
                    className={cn(
                      "w-full rounded-md border p-3 text-left text-sm transition-colors hover:bg-accent/50",
                      competitionId === competition.external_id && "border-primary bg-accent",
                    )}
                  >
                    <span className="font-medium">{competition.name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {competition.country ?? "—"}
                      {competition.season ? ` · ${competition.season.label}` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-2">
          <h2 className="text-lg font-semibold">Teams</h2>
          {!competitionId ? (
            <p className="text-sm text-muted-foreground">Select a competition.</p>
          ) : teams.isLoading ? (
            <Skeletons />
          ) : teams.isError ? (
            <p className="text-sm text-destructive">Could not load teams.</p>
          ) : (
            <ul className="space-y-1">
              {teams.data?.map((team) => (
                <li key={team.external_id} className="rounded-md border p-3 text-sm">
                  <span className="font-medium">{team.name}</span>
                  <span className="block text-xs text-muted-foreground">
                    {team.short_name ?? team.country ?? "—"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {/* Providers */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Providers</h2>
        {providers.isLoading ? (
          <Skeletons rows={2} />
        ) : providers.isError ? (
          <p className="text-sm text-destructive">Could not load providers.</p>
        ) : (
          <ProviderStatusList providers={providers.data ?? []} />
        )}
      </section>
    </div>
  );
}
