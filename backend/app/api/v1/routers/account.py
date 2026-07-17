"""Account, preferences, onboarding, and dashboard endpoints (Stage 10).

    PATCH /me                 -> update profile (display name, timezone)
    GET   /me/preferences     -> notification + display preferences
    PUT   /me/preferences     -> replace preferences
    GET   /onboarding/status  -> computed onboarding progress
    GET   /dashboard          -> aggregated home screen

Profile identity (email, Google link) is owned by the auth stage; this only
edits the mutable profile fields and preferences.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.deps import (
    CurrentUser,
    get_account_service,
    get_dashboard_service,
    get_onboarding_service,
)
from app.application.services.account_service import AccountService
from app.application.services.dashboard_service import DashboardService
from app.application.services.onboarding_service import OnboardingService
from app.schemas.application import (
    DashboardResponse,
    OnboardingStateResponse,
    PreferencesModel,
    PreferencesResponse,
    UpdateProfileRequest,
)
from app.schemas.auth import UserOut

router = APIRouter(tags=["account"])

Account = Annotated[AccountService, Depends(get_account_service)]
Onboarding = Annotated[OnboardingService, Depends(get_onboarding_service)]
Dashboard = Annotated[DashboardService, Depends(get_dashboard_service)]


@router.patch("/me", response_model=UserOut, summary="Update profile")
async def update_profile(
    payload: UpdateProfileRequest, user: CurrentUser, account: Account
) -> UserOut:
    updated = await account.update_profile(
        user, display_name=payload.display_name, timezone=payload.timezone
    )
    return UserOut.model_validate(updated)


@router.get("/me/preferences", response_model=PreferencesResponse, summary="Get preferences")
async def get_preferences(user: CurrentUser, account: Account) -> PreferencesResponse:
    return PreferencesResponse(preferences=await account.get_preferences(user))


@router.put("/me/preferences", response_model=PreferencesResponse, summary="Update preferences")
async def set_preferences(
    payload: PreferencesModel, user: CurrentUser, account: Account
) -> PreferencesResponse:
    saved = await account.set_preferences(user, payload.model_dump())
    return PreferencesResponse(preferences=saved)


@router.get(
    "/onboarding/status", response_model=OnboardingStateResponse, summary="Onboarding state"
)
async def onboarding_status(user: CurrentUser, onboarding: Onboarding) -> OnboardingStateResponse:
    state = await onboarding.state(user)
    return OnboardingStateResponse.model_validate(state.as_dict())


@router.get("/dashboard", response_model=DashboardResponse, summary="Dashboard summary")
async def dashboard(user: CurrentUser, service: Dashboard) -> DashboardResponse:
    return DashboardResponse(**await service.summary(user))
