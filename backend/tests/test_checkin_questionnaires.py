from app.services.checkin_questionnaires import default_checkin_schedule
from app.services.checkin_questionnaires import normalize_checkin_schedule


def test_default_day7_checkin_link_copy_names_checkin():
    schedule = default_checkin_schedule()
    day7 = next(rule for rule in schedule["rules"] if rule["day"] == 7)

    rendered = day7["message"].format(
        day=7,
        personalized_link="https://example.com/s/personalized",
        questionnaire_link="https://example.com/q/stage",
    )

    assert rendered.startswith(
        "晚上好，今天是第7天打卡【仅需10秒】，看看咱们的【专属5项】完成得怎样吧："
    )
    assert "每日行动" not in rendered
    assert "https://example.com/s/personalized" in rendered
    assert "节点复盘" in rendered


def test_normalize_schedule_upgrades_saved_legacy_checkin_copy():
    legacy = {
        "rules": [
            {
                "day": 7,
                "template_key": "stage_review",
                "message": (
                    "晚上好，今天是第 {day} 天。请优先完成您的今日【必填】每日行动：\n"
                    "👉 {personalized_link} (仅需10秒)\n\n"
                    "今天也是首个 7 天复盘日！有空请务必填写一下本次【选填】节点复盘：\n"
                    "📝 {questionnaire_link}"
                ),
            }
        ],
        "daily_rule": {
            "enabled": True,
            "template_key": "daily_checkin_simple",
            "message": (
                "晚上好，今天是第 {day} 天。请优先完成您的今日【必填】每日行动：\n"
                "👉 {personalized_link} (仅需10秒)\n\n"
                "如今天时间充裕，建议也填写一下本次【选填】节点复盘：\n"
                "📝 {questionnaire_link}"
            ),
            "skip_days": [1, 3, 7, 14, 21, 28],
        },
    }

    schedule = normalize_checkin_schedule(legacy)
    day7_message = schedule["rules"][0]["message"]
    daily_message = schedule["daily_rule"]["message"]

    assert "第{day}天打卡【仅需10秒】" in day7_message
    assert "第{day}天打卡【仅需10秒】" in daily_message
    assert "每日行动" not in day7_message
    assert "每日行动" not in daily_message
    assert "节点复盘" in day7_message
    assert "节点复盘" in daily_message
