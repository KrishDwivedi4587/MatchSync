"""Calendar permission rules.

Pure policy over ``CalendarAccessRole``. Separated from validation so the rules
"what may we do with this role" and "is this calendar usable" stay independent.
"""

from __future__ import annotations

from app.domain.value_objects.enums import CalendarAccessRole
from app.exceptions.calendar import CalendarPermissionError

WRITABLE_ROLES = frozenset({CalendarAccessRole.OWNER, CalendarAccessRole.WRITER})
READABLE_ROLES = WRITABLE_ROLES | {CalendarAccessRole.READER}


class CalendarPermissions:
    @staticmethod
    def can_write(role: CalendarAccessRole) -> bool:
        """MatchSync must create/update/delete events -> owner or writer only."""
        return role in WRITABLE_ROLES

    @staticmethod
    def can_read(role: CalendarAccessRole) -> bool:
        return role in READABLE_ROLES

    @staticmethod
    def require_write(role: CalendarAccessRole) -> None:
        if not CalendarPermissions.can_write(role):
            raise CalendarPermissionError(
                f"Calendar access role '{role.value}' does not permit writing events."
            )
