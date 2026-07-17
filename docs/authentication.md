# Authentication (Stage 4)

MatchSync authenticates users with **Google OAuth 2.0** (Authorization Code +
PKCE) and issues its own session: a short-lived **JWT access token** and a
rotating **opaque refresh token**, both in httpOnly cookies. Refresh-session
state lives in **Redis** (instant revocation), so no database schema change was
needed — the Stage 3 persistence layer is untouched.

## How it works

```
Browser                Next.js (/api proxy)         FastAPI                 Google
  | click "Sign in"          |                          |                      |
  |── GET /api/v1/auth/login ─▶ forward ───────────────▶| build URL + state    |
  |◀──────────── 307 to Google consent ────────────────|  (state cookie set)   |
  |──────────────────────────── consent ───────────────────────────────────────▶
  |◀───────────── 307 to /api/v1/auth/callback?code&state ──────────────────────|
  |── GET callback ──▶ forward ─────────────────────────▶ verify state (CSRF)   |
  |                                                       exchange code (PKCE) ──▶
  |                                                       fetch profile ─────────▶
  |                                                       get-or-create user     |
  |                                                       create Redis session   |
  |◀──── 307 to /dashboard  (Set-Cookie: access, refresh) ──────────────────────|
```

- **Access token** (JWT, 15 min): sent on every request; validated statelessly,
  then checked against Redis so logout is instant.
- **Refresh token** (opaque, 30 days): only sent to `/api/v1/auth/*`; rotated on
  every `/auth/refresh`; reuse of a rotated token revokes the whole session.

## Google Cloud Console setup

1. Go to <https://console.cloud.google.com/> → create/select a project.
2. **APIs & Services → OAuth consent screen**: choose *External*, add app name,
   support email, and the scopes `openid`, `email`, `profile`. Add yourself as a
   test user while the app is in "testing".
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - **Authorized redirect URIs** — add exactly:
     - Dev: `http://localhost:3000/api/v1/auth/callback`
     - Prod: `https://YOUR_DOMAIN/api/v1/auth/callback`
   - (The redirect URI points at the frontend host; Next.js proxies `/api` to
     the backend so cookies stay first-party.)
4. Copy the **Client ID** and **Client secret** into your backend `.env`.

## Environment variables (backend)

| Variable | Purpose |
|---|---|
| `GOOGLE_CLIENT_ID` | OAuth client id from the console. |
| `GOOGLE_CLIENT_SECRET` | OAuth client secret. |
| `GOOGLE_REDIRECT_URI` | Must **exactly** match a console redirect URI. |
| `FRONTEND_URL` | Base URL the callback redirects back to. |
| `POST_LOGIN_PATH` / `POST_LOGOUT_PATH` | In-app redirect targets. |
| `SECRET_KEY` | **Must be ≥ 32 bytes in prod.** Signs JWTs and derives the token-encryption key. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | Token lifetimes. |
| `COOKIE_SECURE` | Unset = auto (off local, on otherwise). Force with `true`/`false`. |
| `COOKIE_SAMESITE` | `lax` (default). |
| `COOKIE_DOMAIN` | Leave empty for host-only cookies. |

Frontend: `NEXT_PUBLIC_API_BASE_URL=/api/v1` and `BACKEND_URL` (proxy target).

## Development setup

```bash
docker compose up postgres redis -d     # Redis is required for sessions
# backend/.env: set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET
cd backend && uvicorn app.main:app --reload
cd frontend && npm run dev
# visit http://localhost:3000/login -> "Sign in with Google"
```

Or the whole stack: `docker compose up --build`.

## Production setup

- Serve everything over **HTTPS**; set `ENVIRONMENT=production` → `COOKIE_SECURE`
  becomes `true`, docs are disabled, JSON logs on.
- Generate a strong `SECRET_KEY` (≥ 32 bytes), e.g. `openssl rand -hex 32`.
- Register the production redirect URI in the console and publish the consent
  screen.
- Ensure Redis is reachable and persistent enough for your session SLA.

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/auth/login` | public | Redirect to Google consent. |
| GET | `/api/v1/auth/callback` | public | Handle Google redirect; set cookies. |
| POST | `/api/v1/auth/refresh` | refresh cookie | Rotate refresh + reissue access. |
| POST | `/api/v1/auth/logout` | cookie | Revoke session, clear cookies. |
| GET | `/api/v1/auth/me` | **required** | Current user. |
| GET | `/api/v1/auth/status` | optional | `{ authenticated, user? }`. |

## Troubleshooting

- **`redirect_uri_mismatch`** — the `GOOGLE_REDIRECT_URI` must match a console
  entry character-for-character (scheme, host, port, path, no trailing slash).
- **Logged out immediately / `/me` returns 401** — Redis not running, or the
  session was revoked. Check `docker compose ps redis`.
- **Cookies not set** — ensure you reach the app through the Next.js proxy
  (`http://localhost:3000`), not the backend directly, so cookies are
  first-party; in prod confirm HTTPS + `COOKIE_SECURE=true`.
- **`InsecureKeyLengthWarning`** — `SECRET_KEY` is shorter than 32 bytes; set a
  longer one.
- **Refresh loops / 401 on refresh** — the refresh token was rotated elsewhere
  (reuse detection revoked the session); sign in again.
