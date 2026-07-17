"""Stable content hashing (pure).

A single deterministic hash used across the domain. Parts are joined with an
ASCII unit separator so that ``("ab", "c")`` and ``("a", "bc")`` never collide.

Note: ``domain/calendar/metadata.content_hash`` deliberately keeps its own
32-char truncated variant. Those hashes are already stored on live Google
events; unifying them would silently invalidate every existing event's
"has this changed?" comparison. Different domain, different stored value.
"""

from __future__ import annotations

import hashlib

_SEPARATOR = "\x1f"


def stable_hash(*parts: object) -> str:
    """Deterministic 64-char SHA-256 hex digest over the given parts."""
    joined = _SEPARATOR.join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()
