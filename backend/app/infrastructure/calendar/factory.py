"""Calendar provider factory.

Maps a linked account's provider key -> a ``CalendarProvider`` bound to that
account's credentials. Adding Apple/Outlook/CalDAV later means registering one
more builder here; the service layer is untouched.

Mirrors Stage 1's ProviderRegistry pattern: pure lookup, no per-provider
conditionals anywhere above this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from app.core.logging import get_logger
from app.domain.ports.calendar_provider import CalendarProvider
from app.domain.value_objects.enums import CalendarProvider as ProviderKey
from app.exceptions.calendar import UnsupportedProviderError
from app.infrastructure.google.calendar_client import GoogleCalendarProvider
from app.infrastructure.google.token_manager import GoogleTokenManager
from app.infrastructure.http.resilient import ResilientHttpClient

logger = get_logger(__name__)

# A builder receives the account id and returns a provider bound to it.
ProviderBuilder = Callable[[uuid.UUID], CalendarProvider]


class CalendarProviderFactory:
    def __init__(self, token_manager: GoogleTokenManager, http: ResilientHttpClient) -> None:
        self._builders: dict[ProviderKey, ProviderBuilder] = {}
        # Google is the only provider implemented in this stage.
        self.register(
            ProviderKey.GOOGLE,
            lambda account_id: GoogleCalendarProvider(account_id, token_manager, http),
        )

    def register(self, key: ProviderKey, builder: ProviderBuilder) -> None:
        self._builders[key] = builder

    def supports(self, key: ProviderKey) -> bool:
        return key in self._builders

    def for_account(self, provider: ProviderKey, account_id: uuid.UUID) -> CalendarProvider:
        builder = self._builders.get(provider)
        if builder is None:
            logger.warning("calendar.provider.unsupported", provider=provider.value)
            raise UnsupportedProviderError(f"No calendar provider for '{provider.value}'.")
        return builder(account_id)
