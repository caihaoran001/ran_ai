"""SSE 实时待办流的连接安全回归测试。

背景：/events/stream 在 while 长循环里每隔几秒 `async with SessionLocal()` 查一次待办数。
管理员刷新/关标签/网络抖动会取消这个长连接协程，若取消正好落在数据库查询那一步，
asyncpg 连接来不及 checkin 回连接池，就会泄漏成 non-checked-in 连接，
累积到连接池上限(默认 5+10=15)后后端所有 DB 操作排队卡死——
表现为"跑一两天整站打不开、群机器人也不说话，重启前端才恢复"。

修复：用 asyncio.shield 把「查询 + 连接归还」整体罩住，取消不会中途打断它。
本测试驱动真实的 event_stream generator，在查询途中取消，验证查询仍完整跑完。
若有人移除了 shield，本测试会失败。
"""
import asyncio

import pytest

import app.api.routes.events as events_mod


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


async def test_event_stream_shields_db_query_against_client_disconnect(monkeypatch):
    finished = {"count": 0}

    async def slow_review_counts():
        # 模拟查询耗时窗口；只有完整执行到底(等价于连接已归还连接池)才会 +1
        await asyncio.sleep(0.15)
        finished["count"] += 1
        return {
            "pending_knowledge_drafts": 0,
            "pending_documents": 0,
            "pending_total": 0,
        }

    monkeypatch.setattr(events_mod, "_review_counts", slow_review_counts)

    response = await events_mod.event_stream(_FakeRequest())
    gen = response.body_iterator

    pull = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.05)  # 此刻正卡在 _review_counts 内部(被 shield 保护)
    pull.cancel()              # 模拟浏览器断开/刷新，取消正在进行的拉取
    with pytest.raises(asyncio.CancelledError):
        await pull

    # 给被 shield 的内部协程留出跑完(归还连接)的时间
    await asyncio.sleep(0.3)
    assert finished["count"] == 1, (
        "客户端断开时 _review_counts 被中途取消、未跑完——"
        "说明 asyncio.shield 缺失或失效，连接会泄漏(回归本次连接池耗尽故障)"
    )

    await gen.aclose()
