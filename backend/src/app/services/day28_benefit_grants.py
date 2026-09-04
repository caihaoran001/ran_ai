from __future__ import annotations

"""第28天权益的发放台账：谁拿到了哪张券的领取入口。

"同一服务单同一张券只发一次"由数据库唯一约束保证，不靠代码先查再插——调度器会
因为漏扫补偿、进程重启重放等原因把同一批候选跑多次，而重复发券是直接的钱损失。
预约模式照 `reminder_service._reserve_reminder_event`：抢到唯一约束才算拿到名额，
抢不到就跳过，不抛错。
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.day28_benefit_grant import Day28BenefitGrant
from app.services.day28_benefit_settings import Day28Benefit, Day28BenefitConfig

STATUS_LINK_SENT = "link_sent"


async def granted_codes(
    session: AsyncSession, *, service_case_id: int
) -> set[str]:
    rows = await session.scalars(
        select(Day28BenefitGrant.benefit_code).where(
            Day28BenefitGrant.service_case_id == service_case_id
        )
    )
    return set(rows.all())


async def reserve_grant(
    session: AsyncSession,
    *,
    service_case_id: int,
    benefit_code: str,
    granted_at: datetime | None = None,
) -> Day28BenefitGrant | None:
    """抢一张券的发放名额；已经发过则返回 None（调用方跳过，不报错）。

    刻意先占名额、后发消息：反过来（先发后记）在发送成功但记账前崩溃时会重复发券。
    发送失败时由调用方回滚这一笔，名额随之释放，下一轮重来。
    """
    grant = Day28BenefitGrant(
        service_case_id=service_case_id,
        benefit_code=benefit_code,
        status=STATUS_LINK_SENT,
        granted_at=granted_at or datetime.now(tz=timezone.utc),
    )
    try:
        async with session.begin_nested():
            session.add(grant)
            await session.flush()
    except IntegrityError:
        return None
    return grant


async def benefits_to_grant(
    session: AsyncSession,
    *,
    service_case_id: int,
    config: Day28BenefitConfig,
    cross_product_intent: bool,
) -> list[Day28Benefit]:
    """本次真正要发的券：配置上可发、且这个服务单还没拿过的。

    返回空列表的含义是"这条权益消息没有内容可发"——由调用方决定是不发消息还是
    改成通知班主任人工处理，这里不替它决定。
    """
    already = await granted_codes(session, service_case_id=service_case_id)
    return [
        benefit
        for benefit in config.grantable_for(cross_product_intent=cross_product_intent)
        if benefit.code not in already
    ]
