"""题库引用的 FAQ 版本 vs 数据库实际版本。

题库里的权威事实是手抄快照，生产 FAQ 由知识进化链路自动升版本，两边没有任何
联动。刻度那条从 v3 涨到 v5 隔了近一个月，题库一无所知，直到真跑评测才发现。
这道校验把漂移在评测开始时就叫出来，而不是让判官把"模型答对了新口径"记成失败。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.reply_agent_cases import ReplyAgentEvalSuite
from app.models.knowledge_faq import KnowledgeFAQ

_PROVENANCE_RE = re.compile(r"knowledge_faqs:(faq_[0-9a-f]+):v(\d+)")


@dataclass(frozen=True)
class FaqProvenanceDrift:
    faq_uid: str
    cited_version: int
    actual_version: int | None
    status: str | None
    case_ids: tuple[str, ...]

    @property
    def kind(self) -> str:
        if self.actual_version is None:
            return "missing"
        if self.status != "active":
            return "inactive"
        return "stale"

    def describe(self) -> str:
        cases = ", ".join(self.case_ids)
        if self.kind == "missing":
            return f"{self.faq_uid} 在库中不存在（题库引用 v{self.cited_version}）：{cases}"
        if self.kind == "inactive":
            return (
                f"{self.faq_uid} 当前 status={self.status}"
                f"（题库引用 v{self.cited_version}）：{cases}"
            )
        return (
            f"{self.faq_uid} 题库引用 v{self.cited_version}，库中已是 "
            f"v{self.actual_version}：{cases}"
        )


def cited_faq_versions(suite: ReplyAgentEvalSuite) -> dict[tuple[str, int], list[str]]:
    """题库里每条 `knowledge_faqs:<uid>:v<n>` 引用，映射到引用它的用例。"""
    cited: dict[tuple[str, int], list[str]] = {}
    for case in suite.cases:
        rag_case = case.rag_case
        if rag_case is None:
            continue
        for fact in rag_case.authoritative_facts:
            match = _PROVENANCE_RE.fullmatch(fact.provenance.strip())
            if match is None:
                continue
            key = (match.group(1), int(match.group(2)))
            bucket = cited.setdefault(key, [])
            if case.case_id not in bucket:
                bucket.append(case.case_id)
    return cited


async def check_faq_provenance(
    session: AsyncSession, suite: ReplyAgentEvalSuite
) -> list[FaqProvenanceDrift]:
    """返回题库快照与库中 FAQ 之间的全部漂移，空列表代表对齐。"""
    cited = cited_faq_versions(suite)
    if not cited:
        return []
    uids = sorted({uid for uid, _ in cited})
    rows = (
        await session.execute(
            select(
                KnowledgeFAQ.faq_uid, KnowledgeFAQ.version, KnowledgeFAQ.status
            ).where(KnowledgeFAQ.faq_uid.in_(uids))
        )
    ).all()
    actual = {row[0]: (row[1], row[2]) for row in rows}
    drifts: list[FaqProvenanceDrift] = []
    for (uid, cited_version), case_ids in sorted(cited.items()):
        found = actual.get(uid)
        if found is None:
            drifts.append(
                FaqProvenanceDrift(uid, cited_version, None, None, tuple(case_ids))
            )
            continue
        version, status = found
        if version != cited_version or status != "active":
            drifts.append(
                FaqProvenanceDrift(uid, cited_version, version, status, tuple(case_ids))
            )
    return drifts


def format_drift_report(drifts: list[FaqProvenanceDrift]) -> str:
    lines = [f"题库引用的 FAQ 版本与库中不一致（{len(drifts)} 条）："]
    lines.extend(f"  - {drift.describe()}" for drift in drifts)
    lines.append(
        "题库的权威答案是手抄快照，FAQ 改版不会自动同步。请人工比对新版口径后更新"
        "题库，或确认无实质变化后把引用版本号改到当前版本。"
    )
    return "\n".join(lines)
