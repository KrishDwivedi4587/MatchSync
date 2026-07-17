"""Google API endpoint constants (single source of truth).

Shared by the Stage 4 OAuth client and the Stage 5 calendar client so the token
endpoint is never defined twice.
"""

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

GOOGLE_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_CALENDAR_BATCH_ENDPOINT = "https://www.googleapis.com/batch/calendarV3"
