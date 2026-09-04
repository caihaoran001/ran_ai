from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class FinalReviewScriptVersion(TimestampMixin, Base):
    """Append-only snapshot of the whole day-26→30 question script for one language.

    整套话术一行一版本（不是一题一行）：运营在后台改一句就追加一个新版本，
    当前生效的是同语言里 version_no 最大的那条。改坏了把旧版本再存一次即可回滚，
    不需要删数据，也不用在题目行上维护各自的版本号。
    """

    __tablename__ = "final_review_script_versions"
    __table_args__ = (
        UniqueConstraint(
            "language",
            "version_no",
            name="ux_final_review_script_language_no",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    language: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # {节点: {"questions": [{"id","text","options","condition"}, ...]}}，
    # 节点取值见 services/final_review_scripts.py::FINAL_REVIEW_NODES。
    nodes_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    changed_by_name: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(String(500))
