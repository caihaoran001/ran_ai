"""安全修复回归测试：问卷 PATCH 越权、图片端点路径前缀校验、入站图片下载 SSRF 白名单。

对应本批安全修复(#2/#3/#4)，#5(回调 fail-closed)在 test_07_qiwe_channel.py 覆盖。
"""
from __future__ import annotations

import pytest

from app.main import _is_public_questionnaire_path
from app.services.ranren_ai.engine import _is_allowed_image_host, _violates_night_a_timing_rule
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)


# ---------- #2 问卷提交越权：PATCH 不再公开豁免，POST 仍公开 ----------

def test_questionnaire_submit_post_is_public():
    assert _is_public_questionnaire_path("/api/questionnaires/abc123/submit", "POST") is True


def test_questionnaire_submit_patch_is_not_public():
    # PATCH=运营编辑重生成，属管理操作，必须走 admin key 鉴权，不能公开
    assert _is_public_questionnaire_path("/api/questionnaires/abc123/submit", "PATCH") is False


def test_questionnaire_get_single_is_public():
    assert _is_public_questionnaire_path("/api/questionnaires/abc123", "GET") is True


def test_questionnaire_list_is_not_public():
    # 列表 GET(无 token 段)不豁免，需 admin key
    assert _is_public_questionnaire_path("/api/questionnaires/", "GET") is False


# ---------- #4 入站图片下载 SSRF 域名白名单 ----------

@pytest.mark.parametrize(
    "url",
    [
        "https://wework.qpic.cn/wwpic/xxx.jpg",
        "http://shmmsns.qpic.cn/a.png",
        "https://imunion.weixin.qq.com/b.jpg",
        "https://wx.qlogo.cn/c.png",
        "https://xxx.myqcloud.com/d.jpg",
    ],
)
def test_image_host_allowed_for_wechat_cdns(url):
    assert _is_allowed_image_host(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/internal",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/health",
        "https://evil.com/x.jpg",
        "https://qpic.cn.evil.com/x.jpg",  # 后缀混淆
        "file:///etc/passwd",
        "ftp://qpic.cn/x",
        "",
    ],
)
def test_image_host_rejected_for_non_whitelisted(url):
    assert _is_allowed_image_host(url) is False


# ---------- #3 图片端点路径前缀校验：拒绝缓存目录外的任意路径 ----------

async def _make_image_message(session, local_path: str) -> int:
    msg = Message(
        group_id="g1",
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="image",
        content="[图片]",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"image_info": {"local_path": local_path, "content_type": "image/jpeg"}},
    )
    session.add(msg)
    await session.flush()
    return msg.id


@pytest.mark.asyncio
async def test_image_endpoint_rejects_path_outside_cache(admin_client, db_session):
    # analysis_json 被污染指向缓存目录外 → 必须 400，不能读取任意文件
    msg_id = await _make_image_message(db_session, "/etc/passwd")
    resp = await admin_client.get(f"/messages/{msg_id}/image")
    assert resp.status_code == 400
    assert "invalid image path" in resp.text


@pytest.mark.asyncio
async def test_image_endpoint_rejects_traversal(admin_client, db_session):
    msg_id = await _make_image_message(db_session, ".wecom_cache/images/../../etc/passwd")
    resp = await admin_client.get(f"/messages/{msg_id}/image")
    assert resp.status_code == 400
    assert "invalid image path" in resp.text


# ---------- 晚A分时管理兜底：不得把晚A建议到午餐/中午 ----------

@pytest.mark.parametrize(
    "reply",
    [
        "晚A跟着午餐一起喝，不用等到睡前。",
        "中午把晚A补上就行，和午B一起喝。",
        "晚A随午饭喝更稳。",
    ],
)
def test_night_a_timing_rule_blocks_midday_advice(reply):
    assert _violates_night_a_timing_rule(reply) is True


@pytest.mark.parametrize(
    "reply",
    [
        "晚A按你的方案放在晚上/睡前喝，别和午B混到一起。",
        "正常作息不要把晚A放到午餐，除非你中午才是睡前。",
        "晚A睡前1-2小时喝就可以，按班主任刚刚说的来。",
    ],
)
def test_night_a_timing_rule_allows_corrections_and_bedtime(reply):
    assert _violates_night_a_timing_rule(reply) is False
