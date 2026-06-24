"""短链接：生成/复用/解析 + 公开解析 endpoint 回归测试。"""
import pytest

from app.services.short_link import get_or_create_short_link, resolve_short_link


@pytest.mark.asyncio
async def test_short_link_create_reuse_resolve(db_session):
    code1 = await get_or_create_short_link(
        db_session, service_case_id=None, template_key="daily_checkin_simple",
        target_path="/q/tokAAA?template=daily_checkin_simple",
    )
    assert len(code1) == 6

    # 同 (case, template) 复用同一短码
    code2 = await get_or_create_short_link(
        db_session, service_case_id=None, template_key="daily_checkin_simple",
        target_path="/q/tokAAA?template=daily_checkin_simple",
    )
    assert code1 == code2

    # 不同 template → 不同短码
    code3 = await get_or_create_short_link(
        db_session, service_case_id=None, template_key="personalized_checkin",
        target_path="/q/tokAAA?template=personalized_checkin",
    )
    assert code3 != code1

    # 解析
    assert await resolve_short_link(db_session, code1) == "/q/tokAAA?template=daily_checkin_simple"
    assert await resolve_short_link(db_session, "zzzzzz") is None


@pytest.mark.asyncio
async def test_short_link_endpoint_public(public_client, db_session):
    code = await get_or_create_short_link(
        db_session, service_case_id=None, template_key="endpoint_test",
        target_path="/q/tokBBB?template=endpoint_test",
    )
    await db_session.flush()

    # public_client 不带 admin key，应能解析(endpoint 已豁免)
    r = await public_client.get(f"/short-links/{code}")
    assert r.status_code == 200
    assert r.json()["target_path"] == "/q/tokBBB?template=endpoint_test"

    r404 = await public_client.get("/short-links/zzzzzz")
    assert r404.status_code == 404
