"""Unit tests for the pure orchestration domain: job state machine + retry policy."""

from __future__ import annotations

import random
import uuid

import pytest

from app.domain.orchestration.models import (
    InvalidTransitionError,
    Job,
    JobPriority,
    JobState,
    JobType,
    Queue,
    can_transition,
)
from app.domain.orchestration.retry import (
    FailureKind,
    RetryPolicy,
    classify,
    decide,
)
from app.exceptions.base import (
    AuthenticationError,
    NotFoundError,
    PermanentError,
    RetryableError,
    ValidationAppError,
)
from app.exceptions.calendar import (
    CalendarReauthRequiredError,
    QuotaExceededError,
)
from app.exceptions.provider import ProviderUnavailableError, RateLimitError


def job(**overrides) -> Job:
    defaults = {
        "type": JobType.SYNC_SUBSCRIPTION,
        "payload": {"subscription_id": str(uuid.uuid4())},
    }
    defaults.update(overrides)
    return Job(**defaults)


# --- state machine ---------------------------------------------------------
def test_happy_path_transitions() -> None:
    j = job()
    assert j.state is JobState.PENDING
    j.transition(JobState.QUEUED)
    assert j.queued_at is not None
    j.transition(JobState.RUNNING)
    assert j.attempts == 1 and j.started_at is not None
    j.transition(JobState.SUCCEEDED)
    assert j.finished_at is not None and j.is_terminal


def test_running_increments_attempts_each_time() -> None:
    j = job()
    j.transition(JobState.QUEUED)
    j.transition(JobState.RUNNING)
    j.transition(JobState.RETRYING)
    j.transition(JobState.QUEUED)
    j.transition(JobState.RUNNING)
    assert j.attempts == 2


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobState.SUCCEEDED, JobState.RUNNING),
        (JobState.CANCELLED, JobState.QUEUED),
        (JobState.SKIPPED, JobState.RUNNING),
        (JobState.PENDING, JobState.RUNNING),
        (JobState.QUEUED, JobState.SUCCEEDED),
        (JobState.PENDING, JobState.FAILED),
    ],
)
def test_illegal_transitions_raise(current, target) -> None:
    j = job(state=current)
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransitionError):
        j.transition(target)


def test_a_job_may_fail_before_it_ever_runs() -> None:
    """No handler / bad payload: it failed, but it never attempted."""
    j = job()
    j.transition(JobState.QUEUED)
    j.transition(JobState.FAILED)
    assert j.attempts == 0 and j.finished_at is not None


def test_dead_letter_can_only_be_manually_requeued() -> None:
    assert can_transition(JobState.DEAD_LETTER, JobState.QUEUED)
    assert not can_transition(JobState.DEAD_LETTER, JobState.RUNNING)


def test_skipped_is_terminal_and_never_retried() -> None:
    j = job()
    j.transition(JobState.QUEUED)
    j.transition(JobState.SKIPPED)
    assert j.is_terminal
    assert can_transition(JobState.SKIPPED, JobState.QUEUED) is False


# --- routing ---------------------------------------------------------------
def test_manual_sync_goes_to_the_high_priority_queue() -> None:
    assert job(priority=JobPriority.HIGH).queue is Queue.SYNC_HIGH
    assert job(priority=JobPriority.NORMAL).queue is Queue.SYNC_DEFAULT


def test_queue_routing_by_type() -> None:
    assert job(type=JobType.FIXTURE_IMPORT).queue is Queue.INGEST
    assert job(type=JobType.METADATA_REFRESH).queue is Queue.MAINTENANCE
    assert job(type=JobType.CLEANUP).queue is Queue.MAINTENANCE


def test_sync_and_reconcile_share_a_lock_key() -> None:
    """Both mutate the same subscription's events; at most one may run."""
    sub = str(uuid.uuid4())
    a = job(type=JobType.SYNC_SUBSCRIPTION, payload={"subscription_id": sub})
    b = job(type=JobType.RECONCILE, payload={"subscription_id": sub})
    assert a.lock_key == b.lock_key == f"sync:subscription:{sub}"


def test_harmless_jobs_take_no_lock() -> None:
    assert job(type=JobType.CLEANUP).lock_key is None
    assert job(type=JobType.HEALTH_CHECK).lock_key is None


def test_serialization_roundtrip() -> None:
    j = job(priority=JobPriority.HIGH, user_id=uuid.uuid4())
    j.transition(JobState.QUEUED)
    j.transition(JobState.RUNNING)
    restored = Job.from_dict(j.to_dict())
    assert restored.id == j.id
    assert restored.state is j.state
    assert restored.priority is j.priority
    assert restored.attempts == j.attempts
    assert restored.user_id == j.user_id


def test_latency_and_duration_are_none_before_they_are_known() -> None:
    j = job()
    assert j.queue_latency_seconds is None and j.duration_seconds is None
    j.transition(JobState.QUEUED)
    j.transition(JobState.RUNNING)
    j.transition(JobState.SUCCEEDED)
    assert j.queue_latency_seconds is not None and j.duration_seconds is not None


# --- retry classification ----------------------------------------------------
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RateLimitError(), FailureKind.RATE_LIMITED),
        (QuotaExceededError(), FailureKind.RATE_LIMITED),
        (ProviderUnavailableError(), FailureKind.TRANSIENT),
        (RetryableError(), FailureKind.TRANSIENT),
        (PermanentError(), FailureKind.PERMANENT),
        (AuthenticationError(), FailureKind.PERMANENT),
        (CalendarReauthRequiredError(), FailureKind.PERMANENT),
        (ValidationAppError(), FailureKind.PERMANENT),
        (NotFoundError(), FailureKind.PERMANENT),
        (ConnectionResetError("boom"), FailureKind.TRANSIENT),
    ],
)
def test_failure_classification(exc, expected) -> None:
    assert classify(exc) is expected


def test_permanent_failures_are_never_retried() -> None:
    policy = RetryPolicy(max_attempts=5)
    decision = decide(CalendarReauthRequiredError(), attempts=1, policy=policy)
    assert decision.retry is False and decision.dead_letter is True


def test_transient_failures_retry_until_the_budget_is_spent() -> None:
    policy = RetryPolicy(max_attempts=3)
    assert decide(RetryableError(), 1, policy).retry is True
    assert decide(RetryableError(), 2, policy).retry is True
    exhausted = decide(RetryableError(), 3, policy)
    assert exhausted.retry is False and exhausted.dead_letter is True


def test_backoff_grows_exponentially_and_is_capped() -> None:
    policy = RetryPolicy(base_delay_seconds=10, max_delay_seconds=100)
    rng = random.Random(0)
    # Full jitter: delay ∈ [0, min(cap, base * 2^(n-1))]
    for attempt, ceiling in [(1, 10), (2, 20), (3, 40), (4, 80), (10, 100)]:
        for _ in range(20):
            delay = policy.delay_for(FailureKind.TRANSIENT, attempt, rng=rng)
            assert 0.0 <= delay <= ceiling


def test_rate_limited_failures_wait_at_least_the_floor() -> None:
    policy = RetryPolicy(base_delay_seconds=1, max_delay_seconds=3600, rate_limit_floor_seconds=60)
    rng = random.Random(1)
    for _ in range(20):
        delay = policy.delay_for(FailureKind.RATE_LIMITED, 1, rng=rng)
        assert delay >= 60.0


def test_jitter_actually_spreads_the_herd() -> None:
    """Full jitter must not return the same delay for identical inputs."""
    policy = RetryPolicy(base_delay_seconds=100, max_delay_seconds=1000)
    rng = random.Random(7)
    delays = {policy.delay_for(FailureKind.TRANSIENT, 4, rng=rng) for _ in range(30)}
    assert len(delays) > 20  # highly unlikely to collide if jittered


def test_delay_never_exceeds_the_cap_even_at_high_attempts() -> None:
    policy = RetryPolicy(base_delay_seconds=30, max_delay_seconds=600)
    rng = random.Random(3)
    assert policy.delay_for(FailureKind.TRANSIENT, 50, rng=rng) <= 600.0
