"""把「邀请 N 人加入群聊」换成写着姓名的一句话。

企微的入群通知只给成员 id，不给姓名，正文里也没有任何可读文案。但这些人进群之后
基本都说过话，我们自己的聊天记录里就存着他们的展示名——线上抽样 124/128（97%）能查到，
所以不必为了一个名字去调企微接口。

查名字的顺序：先看群昵称缓存（`content_policy` 那份），再退到本群聊天记录里最近一次
出现的展示名，最后退到全库最近一次。都查不到就保留原来的人数写法。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import bindparam, text

from app.services.content_policy import get_qiwe_group_nickname


logger = logging.getLogger("uvicorn.error")

# 一句话里最多点几个名字，再多就写「等 N 人」，免得系统通知比正经聊天还长
_MAX_NAMES = 5
# 占位名不算真名字，查到了也当没查到
_PLACEHOLDER_NAMES = frozenset({"待完善用户", "待填写问卷用户", "未知用户", "微信用户"})

_SQL_RECENT_SENDER_NAMES = text(
    """
    SELECT DISTINCT ON (sender_id) sender_id, sender_name
      FROM (
          SELECT analysis_json->'_qiwe_meta'->>'sender_id'   AS sender_id,
                 analysis_json->'_qiwe_meta'->>'sender_name' AS sender_name,
                 (group_id = :group_id)                      AS same_group,
                 id
            FROM messages
           WHERE analysis_json->'_qiwe_meta'->>'sender_id' IN :member_ids
             AND coalesce(analysis_json->'_qiwe_meta'->>'sender_name', '') <> ''
      ) t
     ORDER BY sender_id, same_group DESC, id DESC
    """
).bindparams(bindparam("member_ids", expanding=True))


def parse_member_ids(card: dict[str, Any] | None) -> list[str]:
    """从解析结果的附加信息里取出入群成员 id 列表。"""
    if not isinstance(card, dict):
        return []
    raw = str(card.get("member_ids") or "")
    return [part.strip() for part in raw.split(",") if part.strip()]


async def resolve_member_join_text(
    session: Any,
    *,
    group_id: str | None,
    member_ids: list[str],
) -> str:
    """查得到姓名就返回「邀请 张三、李四 进群」，一个都查不到返回空串（调用方保留原文）。"""
    if not member_ids or session is None:
        return ""

    names: dict[str, str] = {}
    try:
        for member_id in member_ids:
            nickname = await get_qiwe_group_nickname(session, group_id, member_id)
            if _usable(nickname):
                names[member_id] = nickname.strip()

        missing = [member_id for member_id in member_ids if member_id not in names]
        if missing:
            rows = await session.execute(
                _SQL_RECENT_SENDER_NAMES,
                {"member_ids": missing, "group_id": str(group_id or "")},
            )
            for row in rows:
                if _usable(row.sender_name):
                    names[row.sender_id] = row.sender_name.strip()
    except Exception as exc:  # noqa: BLE001 — 查不到名字只是显示得糙一点，不能影响消息入库
        logger.warning("resolve member join names failed group_id=%s err=%s", group_id, exc)
        return ""

    ordered = [names[member_id] for member_id in member_ids if member_id in names]
    if not ordered:
        return ""

    total = len(member_ids)
    shown = ordered[:_MAX_NAMES]
    label = "、".join(shown)
    if len(shown) < total:
        return f"邀请 {label} 等 {total} 人进群"
    return f"邀请 {label} 进群"


def _usable(name: Any) -> bool:
    text_value = str(name or "").strip()
    return bool(text_value) and text_value not in _PLACEHOLDER_NAMES
