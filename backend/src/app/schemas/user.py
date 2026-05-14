from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.common import Timestamped
from app.schemas.document import UserProfileRead
from app.schemas.service_case import ServiceCaseRead


class UserListItem(Timestamped):
    id: int
    name: str
    phone: str | None
    wecom_user_id: str | None
    notes: str | None
    deleted_at: datetime | None = None
    service_cases: list[ServiceCaseRead] = Field(default_factory=list)


class UserDetail(UserListItem):
    latest_profile: UserProfileRead | None = None
