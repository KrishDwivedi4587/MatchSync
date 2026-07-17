# Calendar Platform (Stage 5)

MatchSync's "Calendar SDK". Any service can create, update, delete, and query
calendar events without knowing that Google exists.

```python
await calendar_service.list_calendars(user)
await calendar_service.create_event(user, calendar_id, event)
await calendar_service.update_event(user, calendar_id, event_id, event)
await calendar_service.delete_event(user, calendar_id, event_id)
```

## Architecture

```
Application         CalendarService  ·  CalendarValidator  ·  CalendarPermissions
                              │  depends on the port only
Domain (ports)      CalendarProvider  +  CalendarInfo / CalendarEventInput / …
                              ▲  implemented by
Infrastructure      GoogleCalendarProvider ── GoogleTokenManager ── ResilientHttpClient
                              │                      │
                         errors.py (mapping)   oauth_tokens (encrypted, Stage 3)
```

**Google-specific code lives only in `app/infrastructure/google/`.** Everything
above sees provider-agnostic dataclasses and application exceptions.

### Adding a provider

1. Implement `app/domain/ports/calendar_provider.py::CalendarProvider`.
2. Register it in `app/infrastructure/calendar/factory.py`.

That's it — `CalendarService` and every caller are untouched. Apple Calendar,
Outlook (Graph), CalDAV, and ICS all fit this shape.

## OAuth scopes (least privilege)

| Scope | Why |
|---|---|
| `openid`, `email`, `profile` | Identity (Stage 4). |
| `.../auth/calendar.calendarlist.readonly` | Enumerate the user's calendars. |
| `.../auth/calendar.events` | Create/update/delete **events only**. |

We deliberately do **not** request the full `.../auth/calendar` scope, which
would allow creating and deleting entire calendars.

> **Reconnect flow:** users who signed in before Stage 5 hold tokens without the
> calendar scopes. `GET /calendars/status` reports `needs_reauth: true` and the
> settings page shows a "Reconnect Google Calendar" button, which re-runs the
> OAuth flow (`include_granted_scopes` makes this incremental).

## Event metadata strategy

Every event we write carries private extended properties (invisible to the user,
never copied to invitees):

| Key | Meaning |
|---|---|
| `ms_app` | Ownership marker — lets us enumerate only *our* events. |
| `ms_id` | Application id (the sync stage sets this to the fixture identity key). |
| `ms_src` / `ms_src_id` | Source system and its native id. |
| `ms_hash` | Content hash of mutable fields → skip no-op updates. |
| `ms_ver` | Metadata schema version, for in-place migration. |

Event ids are **derived deterministically** from the application id
(`derive_event_id`), so a duplicate insert is rejected by Google itself (409) —
the last line of defence against double events.

## Duplicate detection

`app/domain/calendar/duplicates.py` — pure functions, strongest signal first:

1. Provider event id → 2. `ms_id` → 3. `ms_src`+`ms_src_id` → 4. fuzzy
   (normalized title + start within 30 min).

The synchronization stage builds its reconcile engine on top of these.

## Reliability

- **Retries:** exponential backoff + full jitter, `Retry-After` honoured.
- **Rate limits:** `429` and `403 rateLimitExceeded` → retried.
- **Quota:** `403 quotaExceeded` / `dailyLimitExceeded` → **not** retried
  (`QuotaExceededError`); retrying inside the same window cannot succeed.
- **Outages:** `5xx`/network → retried, then `ProviderUnavailableError`.
- **Token refresh:** transparent, using the encrypted `oauth_tokens` row.
  `invalid_grant` (revoked) → `CalendarReauthRequiredError`.
- **Connection pooling:** one shared `httpx.AsyncClient` per process.
- **Pagination:** all list endpoints follow `nextPageToken`.
- **Batch:** multipart `/batch/calendarV3`, chunked at 50 sub-requests; item
  failures are returned as `BatchResult`, never raised.

### Error mapping

| Google | Application exception | HTTP |
|---|---|---|
| 401 | `CalendarReauthRequiredError` | 401 |
| 403 quota | `QuotaExceededError` | 429 |
| 403 rateLimit | `RateLimitError` (retried) | 429 |
| 403 other | `CalendarPermissionError` | 403 |
| 404 / 410 | `CalendarNotFoundError` | 404 |
| 409 | `EventConflictError` | 409 |
| 5xx / network | `ProviderUnavailableError` | 503 |

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/calendars` | Locally-known calendars + current default. |
| POST | `/api/v1/calendars/refresh` | Re-discover from the provider. |
| GET | `/api/v1/calendars/default` | The selected sync-target calendar. |
| PUT | `/api/v1/calendars/default` | Select a sync-target calendar. |
| GET | `/api/v1/calendars/status` | Connection / scope / permission status. |
| POST | `/api/v1/calendars/validate` | Check a calendar is reachable + writable. |

All require authentication. No synchronization endpoints exist yet.

## Google Calendar limitations to know

- Event ids must be base32hex (`0-9`, `a-v`), 5–1024 chars — handled by
  `derive_event_id`.
- Extended-property keys ≤ 44 chars, values ≤ 1024, ~300 properties per event.
- Batch endpoint accepts ≤ 50 sub-requests; ordering is not guaranteed (we use
  `Content-ID` to re-associate results).
- `accessRole` is only exposed by `calendarList`, not `calendars` — hence
  `get_calendar` reads from `calendarList/{id}`.
- Quotas are per-project *and* per-user; batching reduces request count, not
  quota units.

## Developer guide

```bash
# Existing users must reconnect once to grant the calendar scopes.
# Visit http://localhost:3000/settings/calendar -> "Reconnect Google Calendar"

cd backend && pytest tests/ -k calendar     # calendar test suite
```

Calendars are cached in the `calendars` table (Stage 3, unchanged). Discovery
upserts by `external_calendar_id` and **soft-deletes** calendars that disappear
remotely — history is never destroyed.

## Troubleshooting

- **`needs_reauth: true` with a valid login** — the token predates the calendar
  scopes. Use the Reconnect button.
- **`calendar_permission_denied` when selecting** — the calendar is shared with
  you as reader/freeBusyReader. Pick one you own or can write to.
- **`calendar_quota_exceeded`** — the Google Cloud project's daily quota is
  exhausted. Check the console quota page; retrying immediately will not help.
- **`calendar_provider_unavailable`** — Google 5xx or a network fault after all
  retries. Safe to retry later; the platform is idempotent by event id.
- **Calendar vanished from the list** — it was soft-deleted after discovery
  found it missing remotely. Re-share it and hit Refresh.
