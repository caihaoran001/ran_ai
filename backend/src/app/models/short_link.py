from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ShortLink(TimestampMixin, Base):
    """短链接：把冗长的 /q/{token}?template=xxx 映射成 6 位短码，用 /s/{code} 访问后重定向。

    按 (service_case_id, template_key) 复用同一短码，避免每天发打卡都生成新码。
    """

    __tablename__ = "short_links"
    __table_args__ = (
        Index("ix_short_links_case_template", "service_case_id", "template_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    service_case_id: Mapped[int | None] = mapped_column(ForeignKey("service_cases.id"), index=True)
    template_key: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    # 重定向目标的站内路径，如 /q/{token}?template=daily_checkin_simple
    target_path: Mapped[str] = mapped_column(String(512), nullable=False)
