from app.services.checkin_questionnaires import (
    CHECKIN_REWARD_RECEIPT_PARAGRAPH,
    default_checkin_schedule,
)
from app.services.checkin_questionnaires import normalize_checkin_schedule


def test_default_schedule_only_carries_intake_submit_reply():
    """日打卡链接推送已下线，默认配置只应保留入营问卷回执。"""
    schedule = default_checkin_schedule()

    assert set(schedule.keys()) == {"intake_submit_reply"}
    intake = schedule["intake_submit_reply"]
    assert intake["enabled"] is True
    assert "正在为你生成两份专属方案" in intake["message"]
    assert "{reward_intro}" in intake["message"]
    assert "{plan_start_line}" in intake["message"]
    assert CHECKIN_REWARD_RECEIPT_PARAGRAPH not in intake["message"]


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
    assert "正在为你生成两份专属方案" in empty["intake_submit_reply"]["message"]


def test_default_receipt_renders_the_final_enabled_copy():
    from app.api.routes.questionnaires import _format_template
    from app.services.checkin_questionnaires import CHECKIN_REWARD_RECEIPT_PARAGRAPH

    template = default_checkin_schedule()["intake_submit_reply"]["message"]
    content = _format_template(
        template,
        {
            "reward_intro": CHECKIN_REWARD_RECEIPT_PARAGRAPH,
            "plan_start_line": "你的服务计划于 2026年08月20日 正式开始（打卡也从那天开始）。",
        },
    )

    assert content == (
        "问卷已收到，正在为你生成两份专属方案，完成后会第一时间发到群里。\n\n"
        f"{CHECKIN_REWARD_RECEIPT_PARAGRAPH}\n\n"
        "你的服务计划于 2026年08月20日 正式开始（打卡也从那天开始）。"
    )


def test_reward_receipt_paragraph_follows_tier_config():
    """入营奖励介绍必须跟随档位配置动态生成，关闭或无有效档位时为空。"""
    from app.services.checkin_reward_settings import (
        CheckinRewardTier,
        CheckinRewardTierConfig,
        build_reward_receipt_paragraph,
    )

    assert build_reward_receipt_paragraph(None) == ""
    assert (
        build_reward_receipt_paragraph(
            CheckinRewardTierConfig(
                enabled=False,
                tiers=(CheckinRewardTier(days=3, reward_text="1 管咖啡"),),
            )
        )
        == ""
    )
    assert (
        build_reward_receipt_paragraph(
            CheckinRewardTierConfig(enabled=True, tiers=())
        )
        == ""
    )

    multi = build_reward_receipt_paragraph(
        CheckinRewardTierConfig(
            enabled=True,
            tiers=(
                CheckinRewardTier(days=3, reward_text="1 管咖啡"),
                CheckinRewardTier(days=7, reward_text="1 盒钥匙"),
                CheckinRewardTier(days=14, reward_text="1 瓶C8"),
            ),
        )
    )
    assert "满3、7、14天" in multi
    assert "依次可获得1 管咖啡、再得1 盒钥匙、再得1 瓶C8" in multi

    # 档位删掉就等于不要这一档：剩下的档位照常拼接，被删的名字不再出现。
    trimmed = build_reward_receipt_paragraph(
        CheckinRewardTierConfig(
            enabled=True,
            tiers=(
                CheckinRewardTier(days=3, reward_text="1 管咖啡"),
                CheckinRewardTier(days=14, reward_text="1 瓶C8"),
            ),
        )
    )
    assert "满3、14天" in trimmed
    assert "依次可获得1 管咖啡、再得1 瓶C8" in trimmed
    assert "钥匙" not in trimmed

    single = build_reward_receipt_paragraph(
        CheckinRewardTierConfig(
            enabled=True,
            tiers=(CheckinRewardTier(days=28, reward_text="3 管咖啡"),),
        )
    )
    assert "满28天" in single
    assert "可获得3 管咖啡" in single
