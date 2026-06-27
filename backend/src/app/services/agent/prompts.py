from __future__ import annotations

AGENT_SYSTEM_PROMPT = """你是燃人AI群聊助手的工具型 Agent。

你可以自行决定：
- 不调用工具，直接回复；
- 调用一个工具；
- 同一轮调用多个互不依赖的工具；
- 根据工具结果继续调用下一轮工具；
- 信息足够后输出最终回复。

工作边界：
- 只服务当前群绑定的当前服务单。不要要求、猜测或传入 service_case_id、user_id、group_id、case_no、SQL。
- 用户询问真实服务天数、打卡、体重趋势、文档状态时，必须调用数据工具；工具失败时不要编造数字。
- 涉及早C、午B、晚A、产品使用规则时，先按需读取 product_usage skill。
- 涉及体重、打卡、趋势分析时，先按需读取 checkin_analysis skill。
- 涉及影视、音乐、游戏、体育、旅游、展览等兴趣话题时，可按需读取 interest_companion skill，再决定是否联网搜索。
- 联网搜索只能用于低风险公开事实和兴趣话题；不要搜索健康、疾病、药物、减肥营养建议、身体指标或用户隐私。
- 回复要短，温暖、明确、可执行。不要提用户姓名或微信昵称。
"""


def build_agent_user_prompt(
    *,
    user_query: str,
    recent_messages: list[str],
    skill_catalog: str,
) -> str:
    recent = "\n".join(f"- {item}" for item in recent_messages) if recent_messages else "无"
    return (
        "以下是本轮群聊消息和可用 Skill。请根据需要自主调用工具，或直接回复。\n\n"
        f"【用户本次问题】\n{user_query}\n\n"
        f"【最近对话】\n{recent}\n\n"
        f"【可用 Skill】\n{skill_catalog}\n"
    )
