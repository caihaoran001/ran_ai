"""FAQ「已确认/待确认」闭环——确认状态只用于治理与观测，不改变任何检索行为。

验证：新建即待确认；confirm 指向当前版本并同步 chunk metadata；编辑口径字段
（title/question/answer）自动失效回待确认但保留上次确认记录供追溯；只改
category/tags 等治理字段不升版本、不影响确认状态；unconfirm 清空指向；
停用条目允许确认且与启用状态互不影响。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_faq import KnowledgeFAQVersion
from app.services.knowledge_faq_service import (
    confirm_faq,
    create_faq,
    set_faq_status,
    unconfirm_faq,
    update_faq,
)


@pytest.fixture(autouse=True)
def _clear_socks_proxy_env(monkeypatch):
    """隔离本机 shell 的 ALL_PROXY=socks://：httpx 不认该 scheme，会让未配置 key 的
    Embedding 客户端在构造期就抛错，与被测的确认状态逻辑无关。"""
    for name in ("ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


async def _get_faq_chunk(session, faq_id):
    return await session.scalar(
        select(KnowledgeChunk).where(KnowledgeChunk.chunk_uid == f"faq:{faq_id}").limit(1)
    )


async def _version_count(session, faq_id) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeFAQVersion)
            .where(KnowledgeFAQVersion.faq_id == faq_id)
        )
    )


async def _create_sample_faq(session):
    return await create_faq(
        session,
        title="瓶盖和刻度怎么换算",
        question="一瓶盖是多少？",
        answer="一瓶盖约等于一点五个小刻度。",
        category="工具使用",
        tags=["换算"],
    )


async def test_create_faq_starts_pending_with_chunk_metadata(db_session):
    """新建 FAQ 后为待确认，chunk metadata 同步为 pending。"""
    faq = await _create_sample_faq(db_session)

    assert faq.confirmation_state == "pending"
    assert faq.last_confirmed_version is None
    assert faq.last_confirmed_at is None

    chunk = await _get_faq_chunk(db_session, faq.id)
    assert chunk is not None
    assert chunk.metadata_json["confirm_state"] == "pending"


async def test_confirm_faq_marks_current_version_and_syncs_chunk(db_session):
    """confirm 后指向当前版本，chunk metadata 变为 confirmed 并带版本号。"""
    faq = await _create_sample_faq(db_session)
    confirmed = await confirm_faq(db_session, faq)

    assert confirmed.confirmation_state == "confirmed"
    assert confirmed.last_confirmed_version == confirmed.version
    assert confirmed.last_confirmed_at is not None

    chunk = await _get_faq_chunk(db_session, faq.id)
    assert chunk.metadata_json["confirm_state"] == "confirmed"
    assert chunk.metadata_json["last_confirmed_version"] == confirmed.version


async def test_editing_answer_invalidates_confirmation_but_keeps_memory(db_session):
    """已确认后只改 answer：升版本、回待确认、保留旧确认版本号供追溯。"""
    faq = await _create_sample_faq(db_session)
    old_version = faq.version
    await confirm_faq(db_session, faq)

    updated = await update_faq(db_session, faq, answer="一瓶盖等于 20ml，对应两个刻度。")

    assert updated.version == old_version + 1
    assert updated.confirmation_state == "pending"
    assert updated.last_confirmed_version == old_version
    assert updated.last_confirmed_at is not None

    chunk = await _get_faq_chunk(db_session, faq.id)
    assert chunk.metadata_json["confirm_state"] == "pending"
    assert chunk.metadata_json["last_confirmed_version"] == old_version


async def test_governance_only_update_keeps_version_and_confirmation(db_session):
    """已确认后只改 category/tags：版本号不变、仍 confirmed、不产生新版本行。"""
    faq = await _create_sample_faq(db_session)
    await confirm_faq(db_session, faq)
    count_before = await _version_count(db_session, faq.id)

    updated = await update_faq(db_session, faq, category="饮食", tags=["换算", "工具"])

    assert updated.version == 1
    assert updated.confirmation_state == "confirmed"
    assert await _version_count(db_session, faq.id) == count_before


async def test_reconfirm_after_invalidation_points_to_new_version(db_session):
    """编辑失效后重新 confirm：confirmed 且指向新版本。"""
    faq = await _create_sample_faq(db_session)
    await confirm_faq(db_session, faq)
    updated = await update_faq(db_session, faq, question="一小瓶盖到底是多少？")
    assert updated.confirmation_state == "pending"

    reconfirmed = await confirm_faq(db_session, updated)

    assert reconfirmed.confirmation_state == "confirmed"
    assert reconfirmed.last_confirmed_version == reconfirmed.version


async def test_unconfirm_clears_pointer_back_to_pending(db_session):
    """手动取消确认：两字段清空回待确认。"""
    faq = await _create_sample_faq(db_session)
    await confirm_faq(db_session, faq)

    unconfirmed = await unconfirm_faq(db_session, faq)

    assert unconfirmed.confirmation_state == "pending"
    assert unconfirmed.last_confirmed_version is None
    assert unconfirmed.last_confirmed_at is None

    chunk = await _get_faq_chunk(db_session, faq.id)
    assert chunk.metadata_json["confirm_state"] == "pending"
    assert chunk.metadata_json["last_confirmed_version"] is None


async def test_confirm_disabled_faq_is_independent_of_status(db_session):
    """对停用条目确认：允许成功且不影响 status；enable 后确认状态仍在。"""
    faq = await _create_sample_faq(db_session)
    disabled = await set_faq_status(db_session, faq, "disabled")
    assert disabled.status == "disabled"

    confirmed = await confirm_faq(db_session, disabled)

    assert confirmed.status == "disabled"
    assert confirmed.confirmation_state == "confirmed"

    enabled = await set_faq_status(db_session, confirmed, "active")

    assert enabled.status == "active"
    assert enabled.confirmation_state == "confirmed"


async def test_answer_only_update_writes_version_row(db_session):
    """只改 answer 时产生 KnowledgeFAQVersion 行且 version 正确。"""
    faq = await _create_sample_faq(db_session)
    old_version = faq.version

    updated = await update_faq(db_session, faq, answer="更新后的参考回复。")

    assert updated.version == old_version + 1
    rows = list(
        await db_session.scalars(
            select(KnowledgeFAQVersion).where(KnowledgeFAQVersion.faq_id == faq.id)
        )
    )
    versions = sorted(row.version for row in rows)
    assert versions == [1, old_version + 1]
