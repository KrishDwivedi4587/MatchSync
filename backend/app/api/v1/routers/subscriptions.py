"""Subscription management endpoints (Stage 10).

    GET    /subscriptions            -> the user's subscriptions
    POST   /subscriptions            -> create one
    POST   /subscriptions/bulk       -> create many (duplicates skipped)
    POST   /subscriptions/bulk-delete-> remove many
    GET    /subscriptions/{id}       -> one
    PATCH  /subscriptions/{id}       -> edit frequency / prefix
    DELETE /subscriptions/{id}       -> soft delete
    POST   /subscriptions/{id}/pause -> pause syncing
    POST   /subscriptions/{id}/resume-> resume syncing

Managing subscriptions only. "Sync now" is the existing POST /jobs/sync;
synchronization lives entirely in the untouched engine.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import CurrentUser, get_subscription_service
from app.application.services.subscription_service import (
    SubscriptionInput,
    SubscriptionService,
)
from app.persistence.models.subscription import Subscription
from app.schemas.application import (
    BulkSubscribeRequest,
    BulkUnsubscribeRequest,
    CreateSubscriptionRequest,
    SubscriptionListResponse,
    SubscriptionOut,
    UpdateSubscriptionRequest,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

Service = Annotated[SubscriptionService, Depends(get_subscription_service)]


def _label(subscription: Subscription) -> str:
    if subscription.competition is not None:
        return subscription.competition.name
    if subscription.team is not None:
        return subscription.team.name
    if subscription.sport is not None:
        return f"All {subscription.sport.name}"
    return subscription.scope_type.value


def _out(subscription: Subscription) -> SubscriptionOut:
    return SubscriptionOut(
        id=subscription.id,
        scope=subscription.scope_type,
        status=subscription.status,
        label=_label(subscription),
        sport_key=subscription.sport.key if subscription.sport else None,
        sport_name=subscription.sport.name if subscription.sport else None,
        competition_name=subscription.competition.name if subscription.competition else None,
        team_name=subscription.team.name if subscription.team else None,
        calendar_id=subscription.target_calendar_id,
        calendar_name=(
            subscription.target_calendar.summary if subscription.target_calendar else None
        ),
        sync_frequency_minutes=subscription.sync_frequency_minutes,
        event_prefix=subscription.event_prefix,
        last_synced_at=subscription.last_synced_at,
        next_sync_at=subscription.next_sync_at,
        created_at=subscription.created_at,
    )


def _input(payload: CreateSubscriptionRequest) -> SubscriptionInput:
    return SubscriptionInput(
        calendar_id=payload.calendar_id,
        sport_key=payload.sport,
        scope_type=payload.scope,
        competition_external_id=payload.competition_id,
        team_external_id=payload.team_id,
        sync_frequency_minutes=payload.sync_frequency_minutes,
        event_prefix=payload.event_prefix,
    )


@router.get("", response_model=SubscriptionListResponse, summary="List subscriptions")
async def list_subscriptions(user: CurrentUser, service: Service) -> SubscriptionListResponse:
    subs = await service.list(user)
    return SubscriptionListResponse(subscriptions=[_out(s) for s in subs], total=len(subs))


@router.post(
    "",
    response_model=SubscriptionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a subscription",
)
async def create_subscription(
    payload: CreateSubscriptionRequest, user: CurrentUser, service: Service
) -> SubscriptionOut:
    return _out(await service.create(user, _input(payload)))


@router.post("/bulk", response_model=SubscriptionListResponse, summary="Subscribe to many")
async def bulk_subscribe(
    payload: BulkSubscribeRequest, user: CurrentUser, service: Service
) -> SubscriptionListResponse:
    created = await service.bulk_create(user, [_input(i) for i in payload.items])
    return SubscriptionListResponse(subscriptions=[_out(s) for s in created], total=len(created))


@router.post("/bulk-delete", summary="Unsubscribe from many")
async def bulk_unsubscribe(
    payload: BulkUnsubscribeRequest, user: CurrentUser, service: Service
) -> dict[str, int]:
    return {"deleted": await service.bulk_delete(user, payload.ids)}


@router.get("/{subscription_id}", response_model=SubscriptionOut, summary="Get a subscription")
async def get_subscription(
    subscription_id: uuid.UUID, user: CurrentUser, service: Service
) -> SubscriptionOut:
    return _out(await service.get(user, subscription_id))


@router.patch("/{subscription_id}", response_model=SubscriptionOut, summary="Edit a subscription")
async def update_subscription(
    subscription_id: uuid.UUID,
    payload: UpdateSubscriptionRequest,
    user: CurrentUser,
    service: Service,
) -> SubscriptionOut:
    updated = await service.update(
        user,
        subscription_id,
        sync_frequency_minutes=payload.sync_frequency_minutes,
        event_prefix=payload.event_prefix,
        clear_event_prefix=payload.clear_event_prefix,
    )
    return _out(updated)


@router.delete(
    "/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a subscription"
)
async def delete_subscription(
    subscription_id: uuid.UUID, user: CurrentUser, service: Service
) -> None:
    await service.delete(user, subscription_id)


@router.post("/{subscription_id}/pause", response_model=SubscriptionOut, summary="Pause syncing")
async def pause_subscription(
    subscription_id: uuid.UUID, user: CurrentUser, service: Service
) -> SubscriptionOut:
    return _out(await service.pause(user, subscription_id))


@router.post("/{subscription_id}/resume", response_model=SubscriptionOut, summary="Resume syncing")
async def resume_subscription(
    subscription_id: uuid.UUID, user: CurrentUser, service: Service
) -> SubscriptionOut:
    return _out(await service.resume(user, subscription_id))
