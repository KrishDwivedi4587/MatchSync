"""Pure orchestration domain: job model, state machine, retry policy.

No Celery, no Redis, no I/O. The scheduler decides *when* work runs; it never
decides *how* synchronization works — that is Stage 8's engine, untouched.
"""
