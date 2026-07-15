from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ArchivistExtractTrace(TimestampMixin, Base):
    """档案官抽取的观测留痕:一次抽取(某服务单某自然日)记一行。

    抽取(extract_agent)用裸 ChatOpenAI、无 usage 回调,且在白名单/校验处会**静默丢弃**
    LLM emit 的答案(见 extract_agent 的 continue 分支)——曾导致"上报了却没抽进结构化打卡"
    的漏无法事后复盘。本表把 LLM 原始 tool_calls + 每条答案的保留/丢弃判决及原因落库,
    使同类漏可精确定位。**纯观测产物,非权威事实源**;由 graph.commit 在主事务内写。
    """

    __tablename__ = "archivist_extract_traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_case_id: Mapped[int] = mapped_column(
        ForeignKey("service_cases.id"), nullable=False, index=True
    )
    # 本次抽取所针对的自然日(北京时区分桶);watermark_to 记本轮水位线,
    # 用于事后关联"当时到底喂了哪段增量切片"。
    target_date: Mapped[date | None] = mapped_column(Date)
    watermark_to: Mapped[int | None] = mapped_column(Integer)
    # LLM 原始 tool_calls:[{"name","args"}],原样落库——这是当初缺失的东西。
    emitted_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    # 过滤后存活的摘要(checkin 字段名 / 各桶存活 item_id / outcome·profile 等条数)。
    kept_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # 被丢弃的答案:[{"bucket","item_id","target_date","reason"}]。
    dropped_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
