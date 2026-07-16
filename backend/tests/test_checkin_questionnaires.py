from app.services.checkin_questionnaires import default_checkin_schedule
from app.services.checkin_questionnaires import normalize_checkin_schedule


def test_default_schedule_only_carries_intake_submit_reply():
    """日打卡链接推送已下线，默认配置只应保留入营问卷回执。"""
    schedule = default_checkin_schedule()

    assert set(schedule.keys()) == {"intake_submit_reply"}
    intake = schedule["intake_submit_reply"]
    assert intake["enabled"] is True
    assert "正在生成专属方案" in intake["message"]


def test_normalize_drops_legacy_link_fields():
    """历史存库里残留的 rules/daily_rule/send_at 等字段应被静默丢弃。"""
    legacy = {
        "enabled": True,
        "send_at": "20:00",
        "timezone": "Asia/Shanghai",
        "rules": [
            {
                "day": 7,
                "template_key": "stage_review",
                "message": "旧的打卡链接文案 {questionnaire_link}",
            }
        ],
        "daily_rule": {
            "enabled": True,
            "template_key": "daily_checkin_simple",
            "message": "旧的每日文案 {questionnaire_link}",
            "skip_days": [1, 3, 7, 14, 21, 28],
        },
        "intake_submit_reply": {
            "enabled": False,
            "message": "自定义回执",
        },
    }

    schedule = normalize_checkin_schedule(legacy)

    assert set(schedule.keys()) == {"intake_submit_reply"}
    assert "rules" not in schedule
    assert "daily_rule" not in schedule
    assert "send_at" not in schedule
    assert schedule["intake_submit_reply"] == {
        "enabled": False,
        "message": "自定义回执",
    }


def test_normalize_falls_back_to_default_intake_reply():
    """非法或缺失输入回落到默认入营回执。"""
    schedule = normalize_checkin_schedule(None)
    assert schedule == default_checkin_schedule()

    empty = normalize_checkin_schedule({})
    assert empty["intake_submit_reply"]["enabled"] is True
    assert "正在生成专属方案" in empty["intake_submit_reply"]["message"]
