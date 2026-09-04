from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class FinalReviewAnswer(TimestampMixin, Base):
    """One customer answer to one day-26→30 review question.

    刻意**不加**唯一约束：客户改主意时要保留改口前后两条（V2.0 要求"旧答案被更新时
    保留历史与更新时间，不覆盖得像从未改变"），读取时按 answered_at 取最新即可。

    `answer_status` 区分四种情形，缺一种就会把"客户明确说没有"和"根本没问到"混成
    同一个空值：answered（答了）／skipped（跳过或说不清）／refused（明确拒答）／
    unclear（答了但无法归类）。"没有改善""不购买"都是 answered，不是缺失。
    """

    __tablename__ = "final_review_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"), nullable=False, index=True
    )
    question_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    answer_status: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[Any | None] = mapped_column(JSON)
    evidence_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 答的是哪一版话术。话术在后台随时可改，事后统计口径必须能对回当时问的原话。
    script_version_no: Mapped[int | None] = mapped_column(Integer)
