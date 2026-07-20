"""打卡投影：把聊天抽取出的打卡字段确定性地落到 checkins 表。

铁律（见设计 §2.3 / §7）：
- **表单字段权威，聊天只补空**：用户在打卡表单里填过的字段，聊天推断值绝不覆盖。
- **矛盾不静默改**：同一天表单值 ≠ 聊天值（如体重 100 vs 168）→ 记为 contradiction，
  不覆盖、留痕（供人工发现），而不是盲写。
- **按天 UPSERT + 原地合并**：一天一行(service_case_id, checkin_date)，原地 UPDATE 合并，
  不软删旧行再插（避免留下未过滤 deleted_at 的读取路径读到陈旧行）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.service_case import ServiceCase
from app.services.checkin_presentation import (
    load_personalized_checkin_labels,
)
from app.services.checkin_service import CHECKIN_KEYS

logger = logging.getLogger(__name__)

# product_schedule 是嵌套 dict，按子键合并。
_PRODUCT_SCHEDULE_SUBKEYS = ("morning_c", "noon_b", "night_a", "summary")

SOURCE_CHAT = "chat"
SOURCE_FORM = "form"
# 用户被追问后亲口确认过的字段：权威高于表单。合并时按"非 form"处理（聊天最新值赢），
# 使追问后用户报的新值能覆盖原表单值（见 mark_contradictions_pending_confirmation）。
SOURCE_USER_CONFIRMED = "user_confirmed"


@dataclass
class CheckinUpsertResult:
    created: bool = False
    filled: list[str] = field(default_factory=list)         # 补空成功的字段
    skipped_same: list[str] = field(default_factory=list)    # 值相同、无需改
    kept_form: list[str] = field(default_factory=list)       # 表单已填、聊天不覆盖
    contradictions: list[dict] = field(default_factory=list)  # 表单值≠聊天值，留痕待人工


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, dict):
        return all(_is_empty(v) for v in value.values())
    return False


def _equivalent(a: object, b: object) -> bool:
    return str(a).strip() == str(b).strip()


def _field_source(meta: dict, key: str) -> str:
    """字段来源；缺省视为 form（历史表单打卡无 _meta，其已填字段按权威处理）。"""
    return (meta.get("sources") or {}).get(key, SOURCE_FORM)


def _merge_scalar(
    *, key: str, incoming, parsed: dict, meta: dict, result: CheckinUpsertResult
) -> None:
    cur = parsed.get(key)
    if _is_empty(incoming):
        return
    if _is_empty(cur):
        parsed[key] = incoming
        meta.setdefault("sources", {})[key] = SOURCE_CHAT
        result.filled.append(key)
        return
    if _equivalent(cur, incoming):
        result.skipped_same.append(key)
        return
    # 值不同
    if _field_source(meta, key) == SOURCE_FORM:
        # 表单权威：不覆盖，记矛盾
        result.kept_form.append(key)
        result.contradictions.append(
            {"field": key, "form_value": cur, "chat_value": incoming}
        )
    else:
        # 上一次也是聊天来的：以最新聊天值为准
        parsed[key] = incoming
        result.filled.append(key)


def _merge_product_schedule(
    *, incoming: dict, parsed: dict, meta: dict, result: CheckinUpsertResult
) -> None:
    cur = parsed.get("product_schedule")
    if not isinstance(cur, dict):
        cur = {}
    changed = False
    for sub in _PRODUCT_SCHEDULE_SUBKEYS:
        inc = incoming.get(sub)
        if _is_empty(inc):
            continue
        sub_key = f"product_schedule.{sub}"
        cur_sub = cur.get(sub)
        if _is_empty(cur_sub):
            cur[sub] = inc
            meta.setdefault("sources", {})[sub_key] = SOURCE_CHAT
            result.filled.append(sub_key)
            changed = True
        elif _equivalent(cur_sub, inc):
            result.skipped_same.append(sub_key)
        elif _field_source(meta, sub_key) == SOURCE_FORM:
            result.kept_form.append(sub_key)
            result.contradictions.append(
                {"field": sub_key, "form_value": cur_sub, "chat_value": inc}
            )
        else:
            cur[sub] = inc
            result.filled.append(sub_key)
            changed = True
    if changed or cur:
        parsed["product_schedule"] = cur


def mark_contradictions_pending_confirmation(checkin: Checkin, field_keys: list[str]) -> bool:
    """追问矛盾后调用：把这些字段来源降级为 user_confirmed(不再表单权威)，并把对应矛盾标 asked=true(去重)。

    降级后，用户随后在群里报的新值会走 _merge_scalar 的"聊天最新值赢"分支，覆盖原表单值——
    即"用户被追问确认后可覆盖表单"。返回是否有实际改动。
    """
    if not field_keys:
        return False
    parsed = dict(checkin.parsed_json or {})
    meta = dict(parsed.get("_meta") or {})
    sources = dict(meta.get("sources") or {})
    contradictions = [dict(c) for c in (meta.get("contradictions") or [])]
    targets = set(field_keys)
    changed = False
    for c in contradictions:
        if c.get("field") in targets and not c.get("asked"):
            c["asked"] = True
            changed = True
    for fk in field_keys:
        if sources.get(fk) != SOURCE_USER_CONFIRMED:
            sources[fk] = SOURCE_USER_CONFIRMED
            changed = True
    if not changed:
        return False
    meta["sources"] = sources
    meta["contradictions"] = contradictions
    parsed["_meta"] = meta
    checkin.parsed_json = parsed  # 整体重新赋值，确保 SQLAlchemy 捕获 JSON 变更
    return True


async def _find_active_checkin(
    session: AsyncSession, *, service_case_id: int, checkin_date: date
) -> Checkin | None:
    return await session.scalar(
        select(Checkin).where(
            Checkin.service_case_id == service_case_id,
            Checkin.checkin_date == checkin_date,
            Checkin.deleted_at.is_(None),
        )
    )


async def upsert_checkin(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    checkin_date: date,
    fields: dict,
    evidence_msg_ids: list[int] | None = None,
    content_hint: str = "",
    custom_answers: list[dict] | None = None,
) -> CheckinUpsertResult:
    """把聊天抽取的打卡字段(`fields`)合并进 (service_case, checkin_date) 当天一行。

    补空+标矛盾，表单权威。原地 UPDATE，不软删重插。返回合并结果供审计/追问判断。
    """
    result = CheckinUpsertResult()
    # 只保留合法 CHECKIN_KEYS
    clean = {k: v for k, v in (fields or {}).items() if k in CHECKIN_KEYS and not _is_empty(v)}
    custom_answers = await _with_question_snapshots(
        session,
        service_case_id=service_case.id,
        answers=custom_answers or [],
    )
    if not clean and not custom_answers:
        return result

    checkin = await _find_active_checkin(
        session, service_case_id=service_case.id, checkin_date=checkin_date
    )
    if checkin is None:
        # 新建：全部作为 chat 来源
        parsed: dict = {}
        meta: dict = {"sources": {}, "contradictions": []}
        for key, value in clean.items():
            if key == "product_schedule" and isinstance(value, dict):
                _merge_product_schedule(incoming=value, parsed=parsed, meta=meta, result=result)
            else:
                _merge_scalar(key=key, incoming=value, parsed=parsed, meta=meta, result=result)
        if result.contradictions:
            meta["contradictions"].extend(result.contradictions)
        _merge_custom_answers(parsed, custom_answers)
        # 个性化执行单题目的回答本身也应能创建当天打卡；它不一定同时能映射到
        # 固定的 CHECKIN_KEYS（例如“今天午餐有没有按计划吃”）。
        if not parsed:
            return result
        parsed["_meta"] = meta
        checkin = Checkin(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            user_id=service_case.user_id,
            checkin_date=checkin_date,
            content=content_hint or "（聊天自动归档）",
            raw_message_id=str(evidence_msg_ids[-1]) if evidence_msg_ids else None,
            parsed_json=parsed,
        )
        session.add(checkin)
        result.created = True
        return result

    # 已存在：原地合并
    parsed = dict(checkin.parsed_json or {})
    meta = dict(parsed.get("_meta") or {"sources": {}, "contradictions": []})
    meta.setdefault("sources", {})
    meta.setdefault("contradictions", [])
    for key, value in clean.items():
        if key == "product_schedule" and isinstance(value, dict):
            _merge_product_schedule(incoming=value, parsed=parsed, meta=meta, result=result)
        else:
            _merge_scalar(key=key, incoming=value, parsed=parsed, meta=meta, result=result)
    if result.contradictions:
        meta["contradictions"].extend(result.contradictions)
        logger.warning(
            "Archivist checkin contradiction service_case_id=%s date=%s items=%s",
            service_case.id,
            checkin_date,
            result.contradictions,
        )
    custom_changed = _merge_custom_answers(parsed, custom_answers)
    parsed["_meta"] = meta
    # 只有真正有变化才写(补空/更新/矛盾)，避免无谓 UPDATE
    if result.filled or result.contradictions or custom_changed:
        checkin.parsed_json = parsed
    return result


async def _with_question_snapshots(
    session: AsyncSession,
    *,
    service_case_id: int,
    answers: list[dict],
) -> list[dict]:
    """Resolve every dynamic item against its exact versioned source before persisting it."""
    valid = [item for item in answers if item.get("item_id") and item.get("answer")]
    labels_by_id = await load_personalized_checkin_labels(
        session,
        service_case_id=service_case_id,
        item_ids=[str(item["item_id"]) for item in valid],
    )
    return [
        {
            **item,
            "question": str(item.get("question") or labels_by_id.get(str(item["item_id"])) or "").strip(),
            "source": str(item.get("source") or "chat").strip() or "chat",
        }
        for item in valid
    ]


def _merge_custom_answers(parsed: dict, answers: list[dict]) -> bool:
    """Store answers and immutable question snapshots without polluting fixed fields."""
    changed = False
    current = dict(parsed.get("custom_checkin") or {})
    for item in answers:
        item_id = str(item["item_id"])
        answer = str(item["answer"]).strip()
        if not answer:
            continue
        value = {
            "answer": answer,
            "evidence_message_id": item.get("evidence_message_id"),
            "question": str(item.get("question") or "").strip(),
            "source": str(item.get("source") or "chat").strip() or "chat",
        }
        # 旧聊天归档只有 answer/evidence；新写入补齐快照。空问题不落无意义字段。
        if not value["question"]:
            value.pop("question")
        if current.get(item_id) != value:
            current[item_id] = value
            changed = True
    if changed:
        parsed["custom_checkin"] = current
    return changed
