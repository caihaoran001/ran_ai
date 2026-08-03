from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.user_document import UserDocument


class UserDocumentOriginal(TimestampMixin, Base):
    """首次生成的用户可见交付文件快照。

    当前 ``user_documents`` 行允许运营审核时原地编辑；这里仅保留首次生成、
    运营实际看到的结构化内容与渲染正文，供问题回溯，不参与展示或发送。
    """

    __tablename__ = "user_document_originals"
    __table_args__ = (UniqueConstraint("document_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("user_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[dict | None] = mapped_column(JSON)
    generated_by_model: Mapped[str | None] = mapped_column(String(128))

    document: Mapped[UserDocument] = relationship()
