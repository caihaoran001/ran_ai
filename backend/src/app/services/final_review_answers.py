from __future__ import annotations

"""结营复盘答案的读写与"该问哪道题"的判断。

`normalized_value` 的结构约定（档案官上报时按这个填，见 archivist/report_tools.py）：

- 选择题：`{"selected": ["continue_original", "explore_other"]}`，元素是话术里的
  选项语义 key，不是选项文案、也不是"A/B"这种序号。客户回"A 和 B"由抽取模型
  对照当时的选项清单翻成 key；文案和顺序都可能被运营改过，只有 key 是稳定的。
- 开放题：`{"summary": "腰围松了2公分，睡眠没变化"}`，或干脆留空只保留原话。

四种回答状态缺一不可，混成同一个空值就会把"客户明确说没有"和"根本没问到"当成
一回事：answered（答了，含"没有改善""不购买"这类否定答案）／skipped（跳过、
说不清）／refused（明确拒答）／unclear（答了但无法归类）。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.final_review_answer import FinalReviewAnswer
from app.services.final_review_scripts import (
    ASK_ALWAYS,
    ASK_INTENT_CONTINUE,
    ASK_INTENT_PAUSE,
    ASK_OTHER_HEALTH_NEED,
    Q_CONTACT_PREFERENCE,
    Q_NEXT_GOAL,
    Q_SERVICE_INTENT,
)

STATUS_ANSWERED = "answered"
STATUS_SKIPPED = "skipped"
STATUS_REFUSED = "refused"
STATUS_UNCLEAR = "unclear"
ANSWER_STATUSES: tuple[str, ...] = (
    STATUS_ANSWERED,
    STATUS_SKIPPED,
    STATUS_REFUSED,
    STATUS_UNCLEAR,
)

# R4 的意愿分桶。决定承接那轮问"你愿意继续主要是因为什么"还是"什么让你暂缓"——
# 问错方向比不问更伤人（对刚说要暂停的客户问"你为什么愿意继续"）。
INTENT_CONTINUE = "continue"
INTENT_PAUSE = "pause"

# 明确想继续或想探索的：进"继续"桶。
_CONTINUE_KEYS = frozenset({"continue_original", "explore_other"})
# 先用完库存 / 不买先自己调整 / 不需要后续：进"暂缓"桶。
# `finish_stock` 归这里是因为"现有产品还没用完"本身就是暂缓原因清单里的一项；
# 但它与继续意愿同时出现时，上面那组优先（V2.0：C 可与 A/B 并存，只是时机不同）。
_PAUSE_KEYS = frozenset({"finish_stock", "no_purchase_self", "no_followup"})


@dataclass(frozen=True)
class FinalReviewProgress:
    """一个服务单当前的问答进度：每题最新一条答案。"""

    answers: dict[str, FinalReviewAnswer]

    def status(self, question_id: str) -> str | None:
        row = self.answers.get(question_id)
        return row.answer_status if row is not None else None

    def is_resolved(self, question_id: str) -> bool:
        """这题算问完了没。跳过和拒答也算问完——不能反复追同一题。"""
        return self.status(question_id) in {
            STATUS_ANSWERED,
            STATUS_SKIPPED,
            STATUS_REFUSED,
        }

    def selected_keys(self, question_id: str) -> list[str]:
        row = self.answers.get(question_id)
        if row is None or row.answer_status != STATUS_ANSWERED:
            return []
        return _selected_keys(row.normalized_value)

    @property
    def other_health_need(self) -> bool:
        return "other_health" in self.selected_keys(Q_NEXT_GOAL)

    @property
    def cross_product_intent(self) -> bool:
        """要不要给跨品会员券：客户说想看其他产品，或下一阶段目标就是找日常产品。"""
        return "explore_other" in self.selected_keys(Q_SERVICE_INTENT) or (
            "daily_product" in self.selected_keys(Q_NEXT_GOAL)
        )

    @property
    def intent_bucket(self) -> str | None:
        keys = set(self.selected_keys(Q_SERVICE_INTENT))
        if keys & _CONTINUE_KEYS:
            return INTENT_CONTINUE
        if keys & _PAUSE_KEYS:
            return INTENT_PAUSE
        # 只选了"还没想好"或没答：两套原因问法都不适用，这一题就不问。
        return None

    @property
    def contact_preference_keys(self) -> list[str]:
        return self.selected_keys(Q_CONTACT_PREFERENCE)


def _selected_keys(normalized_value: object) -> list[str]:
    if not isinstance(normalized_value, dict):
        return []
    selected = normalized_value.get("selected")
    if isinstance(selected, str):
        selected = [selected]
    if not isinstance(selected, list):
        return []
    keys: list[str] = []
    for item in selected:
        key = str(item or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


async def load_progress(
    session: AsyncSession, *, service_case_id: int
) -> FinalReviewProgress:
    """取每题最新一条答案。

    表里刻意保留改口历史（同一题可能有多行），所以这里按 answered_at 排序后
    以最后一条为准；answered_at 为空的（例如只记了"问过但没答"）排在最前，
    不会盖掉真实回答。
    """
    rows = list(
        await session.scalars(
            select(FinalReviewAnswer)
            .where(FinalReviewAnswer.service_case_id == service_case_id)
            .order_by(
                FinalReviewAnswer.answered_at.asc().nullsfirst(),
                FinalReviewAnswer.id.asc(),
            )
        )
    )
    latest: dict[str, FinalReviewAnswer] = {}
    for row in rows:
        latest[row.question_id] = row
    return FinalReviewProgress(answers=latest)


async def record_answer(
    session: AsyncSession,
    *,
    service_case_id: int,
    question_id: str,
    answer_status: str,
    raw_text: str | None = None,
    normalized_value: Any | None = None,
    evidence_message_id: int | None = None,
    answered_at: datetime | None = None,
    script_version_no: int | None = None,
) -> FinalReviewAnswer:
    """追加一条答案（不覆盖旧行）。客户改口时两条都在，读取时取最新。"""
    if answer_status not in ANSWER_STATUSES:
        raise ValueError(f"不支持的回答状态：{answer_status}")
    row = FinalReviewAnswer(
        service_case_id=service_case_id,
        question_id=question_id,
        answer_status=answer_status,
        raw_text=(raw_text or None),
        normalized_value=normalized_value,
        evidence_message_id=evidence_message_id,
        answered_at=answered_at or datetime.now(tz=timezone.utc),
        script_version_no=script_version_no,
    )
    session.add(row)
    await session.flush()
    return row


def should_ask(
    question: dict[str, Any],
    progress: FinalReviewProgress,
    *,
    other_health_need_hint: bool = False,
) -> bool:
    """这道题现在该不该问。

    `other_health_need_hint` 留给调用方传"客户此前在群里已提过其他健康困扰"
    （V2.0 要求"主动提及相关需要时"也问），画像侧的判断不在本模块做。
    """
    question_id = str(question.get("id") or "")
    if not question_id or progress.is_resolved(question_id):
        return False
    ask_when = str(question.get("ask_when") or ASK_ALWAYS)
    if ask_when == ASK_ALWAYS:
        return True
    if ask_when == ASK_OTHER_HEALTH_NEED:
        return progress.other_health_need or other_health_need_hint
    if ask_when == ASK_INTENT_CONTINUE:
        return progress.intent_bucket == INTENT_CONTINUE
    if ask_when == ASK_INTENT_PAUSE:
        return progress.intent_bucket == INTENT_PAUSE
    return False


def pending_questions(
    questions: Iterable[dict[str, Any]],
    progress: FinalReviewProgress,
    *,
    other_health_need_hint: bool = False,
) -> list[dict[str, Any]]:
    return [
        question
        for question in questions
        if should_ask(
            question, progress, other_health_need_hint=other_health_need_hint
        )
    ]
