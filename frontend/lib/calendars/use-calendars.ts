"use client";

/**
 * Calendar hooks. Server state lives in TanStack Query (Stage 1's rule);
 * mutations invalidate the calendar queries so the UI stays consistent.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchCalendarStatus,
  fetchCalendars,
  refreshCalendars,
  setDefaultCalendar,
} from "@/lib/calendars/api";
import { queryKeys } from "@/lib/query/keys";

export function useCalendars() {
  return useQuery({
    queryKey: queryKeys.calendars,
    queryFn: fetchCalendars,
    staleTime: 60_000,
  });
}

export function useCalendarStatus() {
  return useQuery({
    queryKey: queryKeys.calendarStatus,
    queryFn: fetchCalendarStatus,
    staleTime: 60_000,
  });
}

function useInvalidateCalendars() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.calendars });
    void queryClient.invalidateQueries({ queryKey: queryKeys.calendarStatus });
  };
}

/** Re-discover calendars from the provider (manual refresh). */
export function useRefreshCalendars() {
  const invalidate = useInvalidateCalendars();
  return useMutation({ mutationFn: refreshCalendars, onSuccess: invalidate });
}

export function useSetDefaultCalendar() {
  const invalidate = useInvalidateCalendars();
  return useMutation({
    mutationFn: (calendarId: string) => setDefaultCalendar(calendarId),
    onSuccess: invalidate,
  });
}
