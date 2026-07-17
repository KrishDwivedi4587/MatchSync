"""Centralized, typed configuration.

Stage 1 mandated a single centralized configuration system with validation and
sensible defaults. All configuration flows through the `Settings` object below —
no module reads `os.environ` directly. Settings are loaded once and cached.

Design notes:
- Values come from environment variables (12-factor). A local ``.env`` file is
  read for developer convenience only; production injects real env vars.
- The app refuses to boot with an invalid configuration (Pydantic validation),
  which is a deliberate fail-fast choice from the architecture doc.
- Secrets are typed as ``SecretStr`` so they never render in logs or reprs.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "development", "staging", "production"]


class Settings(BaseSettings):
    """Application settings, validated at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_name: str = "MatchSync"
    environment: Environment = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Logging -----------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # "json" for machine-ingestible logs (prod), "console" for readable dev logs.
    log_format: Literal["json", "console"] = "json"

    # --- Database (async app driver) --------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "matchsync"
    postgres_password: SecretStr = SecretStr("matchsync")
    postgres_db: str = "matchsync"

    # --- Redis / Celery ----------------------------------------------------
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # --- Security / CORS ---------------------------------------------------
    # Comma-separated in the environment, parsed into a list by Pydantic.
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # --- Secrets / auth ----------------------------------------------------
    secret_key: SecretStr = SecretStr("change-me-in-production")
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")

    # Where Google sends the browser after consent. In dev this is the frontend
    # proxy path (Next.js rewrites /api -> backend) so cookies stay first-party.
    google_redirect_uri: str = "http://localhost:3000/api/v1/auth/callback"
    # Least-privilege scope set: identity + enumerate calendars + manage events.
    # `calendar.events` (not full `calendar`) keeps write access scoped to events.
    google_scopes: list[str] = Field(
        default=[
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
            "https://www.googleapis.com/auth/calendar.events",
        ]
    )

    # Frontend base URL + post-auth redirect targets.
    frontend_url: str = "http://localhost:3000"
    post_login_path: str = "/dashboard"
    post_logout_path: str = "/login"

    # JWT (access tokens).
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    oauth_state_expire_minutes: int = 10

    # Cookies. Secure defaults to on in staging/prod (see is_production).
    cookie_secure: bool | None = None  # None -> derived from environment
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
    access_cookie_name: str = "ms_access"
    refresh_cookie_name: str = "ms_refresh"
    oauth_state_cookie_name: str = "ms_oauth_state"

    # --- Sports providers (Stage 6) ---------------------------------------
    # Base URLs + API keys per provider. A provider with a blank key is
    # registered but reports `configured: false` and refuses network calls.
    football_api_base_url: str = "https://api.football-data.org/v4"
    football_api_key: SecretStr = SecretStr("")

    valorant_api_base_url: str = "https://vlrggapi.vercel.app"
    valorant_api_key: SecretStr = SecretStr("")

    basketball_api_base_url: str = "https://api.balldontlie.io/v1"
    basketball_api_key: SecretStr = SecretStr("")

    # Metadata cache TTL. Reference data changes rarely; provider quotas do not.
    sports_cache_ttl_seconds: int = 3600

    # --- Fixture ingestion (Stage 7) ---------------------------------------
    # Import policy windows: how far back/forward a fetched fixture is accepted.
    fixture_import_past_days: int = 7
    fixture_import_future_days: int = 120
    # Rows per bulk INSERT/UPDATE round-trip. Bounds memory on large imports.
    fixture_import_batch_size: int = 500
    # Fuzzy-match tolerance for the participants+kickoff dedup rung.
    fixture_match_tolerance_hours: int = 12

    # --- Synchronization engine (Stage 8) ----------------------------------
    # The window of fixtures the engine considers. Never a full-table scan.
    sync_window_past_days: int = 1
    sync_window_future_days: int = 90
    # Calendar batch size (Google caps batch sub-requests at 50).
    sync_batch_size: int = 50
    # Safety valves: bound work and memory per run.
    sync_max_fixtures: int = 5000
    sync_max_actions: int = 2000
    # Default event length when a fixture has no explicit end time.
    sync_default_event_duration_minutes: int = 120
    # Dead-letter budget: after this many failures a unit is flagged, not retried.
    sync_max_item_retries: int = 3
    sync_retry_window_days: int = 7
    # Policies.
    sync_conflict_policy: Literal["fixture_wins", "user_wins"] = "fixture_wins"
    sync_cancelled_policy: Literal["annotate", "delete"] = "annotate"
    # When the plan is empty, write nothing at all (see idempotency invariant I5).
    sync_skip_empty_runs: bool = True

    # --- Orchestration platform (Stage 9) ----------------------------------
    # How often Beat scans for subscriptions whose next_sync_at has passed.
    scheduler_scan_interval_minutes: int = 5
    # Max subscriptions enqueued per scan tick (bounds a cold-start stampede).
    scheduler_scan_batch_size: int = 500
    # Lock lease. Shorter than the task time limit; renewed at ttl/3.
    lock_ttl_seconds: int = 120
    # Retry policy for orchestrated jobs.
    job_max_attempts: int = 5
    job_retry_base_delay_seconds: float = 30.0
    job_retry_max_delay_seconds: float = 3600.0
    job_rate_limit_floor_seconds: float = 60.0
    # Job documents live in Redis for this long (audit trail is in Postgres).
    job_retention_seconds: int = 604_800  # 7 days
    # A RUNNING job older than this lost its worker.
    stuck_job_threshold_seconds: int = 1800
    # Worker/scheduler heartbeat expiry.
    heartbeat_ttl_seconds: int = 60
    # Celery worker resource limits.
    worker_soft_time_limit_seconds: int = 600
    worker_time_limit_seconds: int = 660
    worker_prefetch_multiplier: int = 1
    worker_max_tasks_per_child: int = 200
    # Protects Google's per-user quota when many workers run in parallel.
    sync_task_rate_limit: str = "120/m"

    # --- Derived DSNs ------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url_async(self) -> str:
        """SQLAlchemy async URL (asyncpg) used by the application at runtime."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url_sync(self) -> str:
        """SQLAlchemy sync URL (psycopg) used by Alembic migrations."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment in ("staging", "production")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cookies_secure(self) -> bool:
        """Effective Secure flag: explicit override, else on outside local dev."""
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment != "local"


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton settings instance.

    Cached so configuration is parsed and validated exactly once per process.
    Use this everywhere instead of instantiating ``Settings`` directly.
    """
    return Settings()
