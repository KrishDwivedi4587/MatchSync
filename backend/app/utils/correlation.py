"""Correlation-id context.

A ``request_id`` (and, in later stages, a ``run_id`` for sync jobs) is stored in
a context variable so it is available to the logger anywhere in the call stack
without threading it through every function signature. Set by middleware on the
API side and by task wrappers on the worker side.
"""

from contextvars import ContextVar

# Populated per request by RequestIDMiddleware; read by the logging processor.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
