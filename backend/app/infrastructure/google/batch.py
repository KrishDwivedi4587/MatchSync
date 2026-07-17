"""Google batch request encoding/decoding (multipart/mixed).

Pure functions so the fiddly wire format is unit-testable without a network.
Google's calendar batch endpoint accepts up to 50 sub-requests per call; the
caller is responsible for chunking.

Sub-request responses are returned in ``Content-ID`` order, which we set to the
sub-request index so results can be zipped back to inputs deterministically
(Google does not guarantee body ordering).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

MAX_BATCH_SIZE = 50

_T = TypeVar("_T")


@dataclass(frozen=True)
class BatchRequest:
    method: str
    path: str  # relative, e.g. "/calendar/v3/calendars/x/events"
    body: dict[str, Any] | None = None


@dataclass(frozen=True)
class BatchResponse:
    index: int
    status_code: int
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)


def build_batch_body(requests: list[BatchRequest], boundary: str) -> bytes:
    """Encode sub-requests into a multipart/mixed payload."""
    parts: list[str] = []
    for index, req in enumerate(requests):
        lines = [
            f"--{boundary}",
            "Content-Type: application/http",
            f"Content-ID: <item-{index}>",
            "",
            f"{req.method.upper()} {req.path} HTTP/1.1",
        ]
        if req.body is not None:
            payload = json.dumps(req.body)
            lines += [
                "Content-Type: application/json; charset=UTF-8",
                f"Content-Length: {len(payload.encode())}",
                "",
                payload,
            ]
        else:
            lines.append("")
        lines.append("")
        parts.append("\r\n".join(lines))

    parts.append(f"--{boundary}--\r\n")
    return "\r\n".join(parts).encode()


def _boundary_from_content_type(content_type: str) -> str | None:
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    if not match:
        return None
    return (match.group(1) or match.group(2)).strip()


# MULTILINE: the status line sits after the part's MIME headers, not at offset 0.
_STATUS_RE = re.compile(r"^HTTP/\d\.\d\s+(\d{3})", re.MULTILINE)
_CONTENT_ID_RE = re.compile(r"Content-ID:\s*<response-item-(\d+)", re.IGNORECASE)


def parse_batch_response(content: bytes, content_type: str) -> list[BatchResponse]:
    """Decode a multipart/mixed batch response into per-item results.

    Falls back to positional indexing when Google omits the Content-ID header.
    """
    boundary = _boundary_from_content_type(content_type)
    if not boundary:
        return []

    text = content.decode(errors="replace")
    raw_parts = [p for p in text.split(f"--{boundary}") if p.strip() and p.strip() != "--"]

    responses: list[BatchResponse] = []
    for position, part in enumerate(raw_parts):
        status_match = _STATUS_RE.search(part)
        if not status_match:
            continue
        status_code = int(status_match.group(1))

        id_match = _CONTENT_ID_RE.search(part)
        index = int(id_match.group(1)) if id_match else position

        body: dict[str, Any] | None = None
        # The JSON payload (if any) follows the last blank line.
        blank = part.find("\r\n\r\n", status_match.end())
        if blank != -1:
            candidate = part[blank:].strip()
            # Skip the inner headers block and grab the first JSON object.
            brace = candidate.find("{")
            if brace != -1:
                try:
                    body = json.loads(candidate[brace:])
                except json.JSONDecodeError:
                    body = None

        responses.append(BatchResponse(index=index, status_code=status_code, body=body))

    responses.sort(key=lambda r: r.index)
    return responses


def chunk(items: list[_T], size: int = MAX_BATCH_SIZE) -> list[list[_T]]:
    """Split a list into provider-safe batch chunks."""
    return [items[i : i + size] for i in range(0, len(items), size)]
