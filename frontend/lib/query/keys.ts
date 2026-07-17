/**
 * Centralized TanStack Query keys.
 *
 * A single source of truth for cache keys (Stage 1, Section 12) so queries and
 * their invalidations never drift. Feature keys are added here as features land.
 */
export const queryKeys = {
  health: ["health"] as const,
  authStatus: ["auth", "status"] as const,
  calendars: ["calendars"] as const,
  calendarStatus: ["calendars", "status"] as const,
  sports: ["sports"] as const,
  competitions: (sportKey: string) => ["sports", sportKey, "competitions"] as const,
  teams: (sportKey: string, competitionId: string) =>
    ["sports", sportKey, "teams", competitionId] as const,
  providers: ["sports", "providers"] as const,
  sportsSearch: (query: string) => ["sports", "search", query] as const,
  fixtures: (filters: Record<string, unknown>) => ["fixtures", filters] as const,
  fixture: (id: string) => ["fixtures", id] as const,
  importRuns: ["fixtures", "import", "runs"] as const,
  // subscriptions: (userId: string) => ["subscriptions", userId] as const,
  // syncHistory: (subscriptionId: string) => ["sync-history", subscriptionId] as const,
} as const;
