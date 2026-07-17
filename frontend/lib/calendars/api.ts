/** Calendar API calls + types. Thin wrappers over the shared API client. */

import { apiFetch } from "@/lib/api/client";

export interface CalendarDto {
  id: string;
  provider: string;
  external_calendar_id: string;
  summary: string;
  description: string | null;
  time_zone: string | null;
  is_primary: boolean;
  is_sync_target: boolean;
  access_role: string | null;
}

export interface CalendarListResponse {
  calendars: CalendarDto[];
  default_calendar_id: string | null;
}

export interface CalendarStatus {
  connected: boolean;
  account_email: string | null;
  has_calendar_scope: boolean;
  needs_reauth: boolean;
  calendar_count: number;
  default_calendar_id: string | null;
  default_calendar_summary: string | null;
}

export interface ValidationResult {
  valid: boolean;
  writable: boolean;
  access_role: string | null;
  reason: string | null;
}

/** Roles that permit MatchSync to write events. Mirrors the backend policy. */
export const WRITABLE_ROLES = ["owner", "writer"];

export function isWritable(calendar: CalendarDto): boolean {
  return calendar.access_role !== null && WRITABLE_ROLES.includes(calendar.access_role);
}

export function fetchCalendars(): Promise<CalendarListResponse> {
  return apiFetch<CalendarListResponse>("/calendars");
}

export function refreshCalendars(): Promise<CalendarListResponse> {
  return apiFetch<CalendarListResponse>("/calendars/refresh", { method: "POST" });
}

export function fetchCalendarStatus(): Promise<CalendarStatus> {
  return apiFetch<CalendarStatus>("/calendars/status");
}

export function setDefaultCalendar(calendarId: string): Promise<CalendarDto> {
  return apiFetch<CalendarDto>("/calendars/default", {
    method: "PUT",
    body: JSON.stringify({ calendar_id: calendarId }),
  });
}

export function validateCalendar(calendarId: string): Promise<ValidationResult> {
  return apiFetch<ValidationResult>("/calendars/validate", {
    method: "POST",
    body: JSON.stringify({ calendar_id: calendarId }),
  });
}
