/**
 * Shared TypeScript types.
 *
 * In later stages, domain types are generated from the backend OpenAPI schema
 * and re-exported here so the frontend and backend contracts cannot drift.
 */

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
}
