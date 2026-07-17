"""Unit tests for the Google batch multipart encoder/decoder."""

from __future__ import annotations

from app.infrastructure.google.batch import (
    BatchRequest,
    build_batch_body,
    chunk,
    parse_batch_response,
)


def test_build_batch_body_encodes_each_subrequest() -> None:
    body = build_batch_body(
        [
            BatchRequest("POST", "/calendar/v3/calendars/c/events", {"summary": "A"}),
            BatchRequest("DELETE", "/calendar/v3/calendars/c/events/e1"),
        ],
        boundary="B",
    ).decode()

    assert body.count("--B\r\n") == 2
    assert body.rstrip().endswith("--B--")
    assert "Content-ID: <item-0>" in body
    assert "POST /calendar/v3/calendars/c/events HTTP/1.1" in body
    assert '{"summary": "A"}' in body
    assert "DELETE /calendar/v3/calendars/c/events/e1 HTTP/1.1" in body


def test_parse_batch_response_orders_by_content_id() -> None:
    boundary = "batch_abc"
    content = (
        f"--{boundary}\r\n"
        "Content-Type: application/http\r\n"
        "Content-ID: <response-item-1>\r\n\r\n"
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n\r\n"
        '{"id": "second"}\r\n'
        f"--{boundary}\r\n"
        "Content-Type: application/http\r\n"
        "Content-ID: <response-item-0>\r\n\r\n"
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n\r\n"
        '{"id": "first"}\r\n'
        f"--{boundary}--\r\n"
    ).encode()

    results = parse_batch_response(content, f"multipart/mixed; boundary={boundary}")
    assert [r.index for r in results] == [0, 1]
    assert results[0].body == {"id": "first"}
    assert results[1].body == {"id": "second"}


def test_parse_batch_response_captures_item_errors() -> None:
    boundary = "b1"
    content = (
        f"--{boundary}\r\n"
        "Content-Type: application/http\r\n"
        "Content-ID: <response-item-0>\r\n\r\n"
        "HTTP/1.1 409 Conflict\r\n"
        "Content-Type: application/json\r\n\r\n"
        '{"error": {"code": 409, "message": "dup", "errors": [{"reason": "duplicate"}]}}\r\n'
        f"--{boundary}--\r\n"
    ).encode()

    results = parse_batch_response(content, f'multipart/mixed; boundary="{boundary}"')
    assert len(results) == 1
    assert results[0].status_code == 409
    assert results[0].body["error"]["errors"][0]["reason"] == "duplicate"


def test_parse_batch_response_without_boundary_is_empty() -> None:
    assert parse_batch_response(b"junk", "application/json") == []


def test_chunk_respects_max_batch_size() -> None:
    groups = chunk(list(range(120)), size=50)
    assert [len(g) for g in groups] == [50, 50, 20]
