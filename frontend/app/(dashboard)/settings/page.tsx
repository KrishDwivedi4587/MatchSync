"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { type NotificationChannelKey, type Preferences } from "@/lib/app/api";
import { LOGIN_URL } from "@/lib/auth/api";
import { useSession, useLogout } from "@/lib/auth/use-auth";
import { useCalendarStatus } from "@/lib/calendars/use-calendars";
import { usePreferences, useSavePreferences, useUpdateProfile } from "@/lib/app/use-app";
import { toast } from "@/stores/toast-store";

const CHANNELS: { key: NotificationChannelKey; label: string; hasTarget: boolean }[] = [
  { key: "email", label: "Email", hasTarget: true },
  { key: "browser", label: "Browser push", hasTarget: false },
  { key: "discord", label: "Discord webhook", hasTarget: true },
  { key: "slack", label: "Slack webhook", hasTarget: true },
];

// Settings: profile, Google connection, notification preferences, danger zone.
// Notification delivery is not built yet — this configures it for a future stage.
export default function SettingsPage() {
  const { user } = useSession();
  const calendarStatus = useCalendarStatus();
  const prefsQuery = usePreferences();
  const savePrefs = useSavePreferences();
  const updateProfile = useUpdateProfile();
  const logout = useLogout();

  const [displayName, setDisplayName] = useState("");
  const [timezone, setTimezone] = useState("");
  const [prefs, setPrefs] = useState<Preferences | null>(null);

  useEffect(() => {
    if (user) {
      setDisplayName(user.display_name ?? "");
      setTimezone(user.timezone ?? "UTC");
    }
  }, [user]);
  useEffect(() => {
    if (prefsQuery.data) setPrefs(prefsQuery.data.preferences);
  }, [prefsQuery.data]);

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Settings</h1>

      {/* Profile */}
      <Card>
        <CardHeader>
          <CardTitle>Profile</CardTitle>
          <CardDescription>{user?.email}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <label className="block space-y-1 text-sm">
            <span className="text-muted-foreground">Display name</span>
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="text-muted-foreground">Timezone</span>
            <input
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              placeholder="e.g. Europe/London"
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            />
          </label>
          <Button
            size="sm"
            onClick={() =>
              updateProfile.mutate(
                { display_name: displayName, timezone },
                { onSuccess: () => toast.success("Profile saved.") },
              )
            }
            disabled={updateProfile.isPending}
          >
            Save profile
          </Button>
        </CardContent>
      </Card>

      {/* Google */}
      <Card>
        <CardHeader>
          <CardTitle>Google Calendar</CardTitle>
          <CardDescription>
            {calendarStatus.data?.account_email ?? "Not connected"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm text-muted-foreground">
            {calendarStatus.data?.needs_reauth
              ? "Reconnection required."
              : `${calendarStatus.data?.calendar_count ?? 0} calendars available.`}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" asChild>
              <Link href="/settings/calendar">Choose calendar</Link>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <a href={LOGIN_URL}>Reconnect</a>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Notifications (configuration only) */}
      <Card>
        <CardHeader>
          <CardTitle>Notifications</CardTitle>
          <CardDescription>
            Configure how you&apos;d like to be reminded. Delivery is coming soon.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {prefs &&
            CHANNELS.map((channel) => {
              const value = prefs.notifications[channel.key];
              return (
                <div key={channel.key} className="space-y-2 rounded-lg border p-3">
                  <label className="flex items-center justify-between text-sm font-medium">
                    {channel.label}
                    <input
                      type="checkbox"
                      checked={value.enabled}
                      onChange={(e) =>
                        setPrefs({
                          ...prefs,
                          notifications: {
                            ...prefs.notifications,
                            [channel.key]: { ...value, enabled: e.target.checked },
                          },
                        })
                      }
                      className="h-4 w-4"
                    />
                  </label>
                  {channel.hasTarget && value.enabled && (
                    <input
                      value={value.target ?? ""}
                      onChange={(e) =>
                        setPrefs({
                          ...prefs,
                          notifications: {
                            ...prefs.notifications,
                            [channel.key]: { ...value, target: e.target.value },
                          },
                        })
                      }
                      placeholder={channel.key === "email" ? "you@example.com" : "https://…"}
                      className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                    />
                  )}
                </div>
              );
            })}
          <Button
            size="sm"
            onClick={() =>
              prefs &&
              savePrefs.mutate(prefs, { onSuccess: () => toast.success("Preferences saved.") })
            }
            disabled={!prefs || savePrefs.isPending}
          >
            Save preferences
          </Button>
        </CardContent>
      </Card>

      {/* Danger zone */}
      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle className="text-destructive">Danger zone</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm text-muted-foreground">Sign out of MatchSync on this device.</span>
          <Button variant="destructive" size="sm" onClick={() => logout.mutate()} disabled={logout.isPending}>
            {logout.isPending ? "Signing out…" : "Sign out"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
