"""Event metadata strategy — how MatchSync identifies and owns its events.

Every event MatchSync writes carries a small set of *private* key/value pairs
(Google: ``extendedProperties.private``; CalDAV: X- properties; ICS: X- props).
They are invisible to the user, stripped from provider UIs, and queryable.

Keys (all short: Google caps keys at 44 chars, values at 1024, ~300 total props):

    ms_app       ownership marker — "1" on every event we create. Lets us list
                 *only our* events and never touch a user's own entries.
    ms_id        application id — our stable identity for the event. The
                 synchronization stage sets this to the fixture identity key.
    ms_src       source system that produced the event (e.g. a provider key).
    ms_src_id    that source's native id for the underlying object.
    ms_hash      content hash of the mutable fields. Drives no-op skipping:
                 if the stored hash equals the freshly computed one, no write.
    ms_ver       metadata schema version, so a future shape change can migrate
                 events in place rather than orphaning them.

Design decisions:
- **Private, not shared, properties** so nothing leaks into invitee copies.
- **Ownership marker separate from the id** so we can safely enumerate our
  events even when an id scheme changes.
- **Hash stored on the event, not only in our DB**, so the provider remains the
  source of truth for "what did we last push" even if our row is lost.
- **Deterministic event ids** (``derive_event_id``) so a duplicate insert is
  rejected by the provider itself — the last line of defence against doubles.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

APP_MARKER_KEY = "ms_app"
APP_MARKER_VALUE = "1"
KEY_APP_ID = "ms_id"
KEY_SOURCE = "ms_src"
KEY_SOURCE_ID = "ms_src_id"
KEY_CONTENT_HASH = "ms_hash"
KEY_VERSION = "ms_ver"

METADATA_VERSION = "1"


@dataclass(frozen=True)
class EventMetadata:
    """MatchSync's private metadata attached to a calendar event."""

    app_id: str
    source: str | None = None
    source_id: str | None = None
    content_hash: str | None = None
    version: str = METADATA_VERSION

    def to_properties(self) -> dict[str, str]:
        """Serialize to the provider's private key/value map."""
        props: dict[str, str] = {
            APP_MARKER_KEY: APP_MARKER_VALUE,
            KEY_APP_ID: self.app_id,
            KEY_VERSION: self.version,
        }
        if self.source:
            props[KEY_SOURCE] = self.source
        if self.source_id:
            props[KEY_SOURCE_ID] = self.source_id
        if self.content_hash:
            props[KEY_CONTENT_HASH] = self.content_hash
        return props

    @classmethod
    def from_properties(cls, props: dict[str, str]) -> EventMetadata | None:
        """Parse metadata back, or None if the event isn't ours.

        Only the ownership marker is required. ``app_id`` may be absent (older
        events, or events identified solely by their source), in which case it
        is empty and callers fall back to the source key.
        """
        if not is_owned_by_matchsync(props):
            return None
        return cls(
            app_id=props.get(KEY_APP_ID, ""),
            source=props.get(KEY_SOURCE),
            source_id=props.get(KEY_SOURCE_ID),
            content_hash=props.get(KEY_CONTENT_HASH),
            version=props.get(KEY_VERSION, METADATA_VERSION),
        )


def is_owned_by_matchsync(props: dict[str, str]) -> bool:
    """True iff the event carries our ownership marker."""
    return props.get(APP_MARKER_KEY) == APP_MARKER_VALUE


def ownership_filter() -> dict[str, str]:
    """Metadata filter selecting only MatchSync-created events."""
    return {APP_MARKER_KEY: APP_MARKER_VALUE}


def derive_event_id(app_id: str) -> str:
    """Deterministically derive a provider-safe event id from an application id.

    Google requires event ids to be base32hex (chars ``0-9`` and ``a-v``) and
    5-1024 characters. We hash the application id and base32hex-encode it, which
    makes ``create_event`` idempotent: a second insert with the same id is
    rejected with a conflict instead of silently creating a duplicate.
    """
    digest = hashlib.sha256(app_id.encode()).digest()
    encoded = base64.b32hexencode(digest).decode().rstrip("=").lower()
    return encoded[:32]


def content_hash(*parts: object) -> str:
    """Stable hash over an event's mutable fields (title, times, location...)."""
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:32]
