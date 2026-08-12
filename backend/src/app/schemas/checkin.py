from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, computed_field

from app.schemas.common import Timestamped
from app.services.checkin_completion import is_complete_checkin_payload


class CheckinCreate(BaseModel):
    checkin_date: date | None = None
    content: str = Field(min_length=1)
    raw_message_id: str | None = None
    parsed_json: dict | None = None


class CheckinRead(Timestamped):
    id: int
    service_case_id: int
    group_id: str
    user_id: int | None
    checkin_date: date
    content: str
    raw_message_id: str | None
    parsed_json: dict | None

    @computed_field
    @property
    def is_complete(self) -> bool:
        return is_complete_checkin_payload(self.parsed_json)
