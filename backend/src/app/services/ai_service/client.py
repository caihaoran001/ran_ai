from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.core.config import Settings, get_settings


class AIService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = AsyncOpenAI(
            api_key=self.settings.ai_api_key or "missing",
            base_url=self.settings.ai_base_url,
            timeout=self.settings.ai_timeout_seconds,
        )

    async def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        if not self.settings.ai_api_key:
            raise RuntimeError("AI_API_KEY is required")
        response = await self._client.chat.completions.create(
            model=self.settings.ai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.settings.ai_temperature,
            max_tokens=self.settings.ai_max_tokens,
        )
        content = response.choices[0].message.content if response.choices else ""
        return (content or "").strip()

    async def generate_profile(self, answers: dict[str, Any]) -> tuple[dict[str, Any], str]:
        text = await self.generate_text(
            system_prompt=(
                "你是燃AI内部运营助手。基于问卷生成用户画像，并量身制定为期28天的每日固定打卡任务（共5项，这是必填项目，重点追踪用户的个案卡点、习惯纠正与身体指标）。\n"
                "输出 JSON，字段必须包含:\n"
                "- summary (字符串): 用户健康情况总体概述。\n"
                "- state (字符串): 状态评估。\n"
                "- main_problem (字符串): 核心主要问题。\n"
                "- day28_goal (字符串): 28天减重与健康目标。\n"
                "- risks (字符串): 潜在风险评估。\n"
                "- notes (字符串): 给班主任的运营备注。\n"
                "- personalized_checkin_fields (数组): 包含5个专门针对该用户的每日固定打卡任务。每个打卡任务是一个字典，包含以下字段:\n"
                "  - id (字符串): 使用固定的键名，分别为: 'personalized_weight'、'personalized_water'、'personalized_sleep'、'personalized_diet'、'personalized_exercise'。\n"
                "  - key (字符串): 与 id 相同。\n"
                "  - type (字符串): 'SIMPLE' (文本输入框，用于体重等) 或 'RADIO' (单选是/否)。\n"
                "  - title (字符串): 针对该用户的个性化提问文案。例如：\n"
                "    - 'personalized_weight': '今日晨起体重（kg）'\n"
                "    - 'personalized_water': '今日饮水是否达到 X 升？' (根据用户饮水习惯定制目标)\n"
                "    - 'personalized_sleep': '昨晚是否在 XX:XX 前入睡？' (针对该用户的作息习惯定制目标)\n"
                "    - 'personalized_diet': '今日是否做到 XX 饮食改善？' (针对该用户的具体饮食改善目标)\n"
                "    - 'personalized_exercise': '今日是否完成了 XX 运动？' (针对该用户的具体活动习惯定制目标)\n"
                "  - label (字符串): 与 title 相同。\n"
                "  - required (布尔值): 必须为 true。\n"
                "  - options (数组): 如果 type 是 'RADIO'，则必须为 ['是', '否']；如果 type 是 'SIMPLE'，则为空数组 []。"
            ),
            user_prompt=f"问卷答案：\n{json.dumps(answers, ensure_ascii=False, indent=2)}",
        )
        parsed = _parse_json_object(text)
        return parsed, str(parsed.get("summary") or text[:1000])

    async def extract_intervention_insight(
        self,
        *,
        source: str,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = await self.generate_text(
            system_prompt=(
                "你是燃AI的健康服务风控和运营意图识别助手。\n"
                "你的任务是精准识别群聊/问卷中是否存在以下三个等级的风险，并输出结构化 JSON 对象（只输出纯 JSON，不要 Markdown 包装）。\n\n"
                
                "【严格的风险分级标准】\n"
                "- L3 健康高风险（标记 level='high'）：疾病（糖尿病/高血压/甲减/结石）、用药（长期服药/激素药）、孕产哺乳期、体检指标严重异常、强烈过敏反应、抑郁/自残心理危机、疗效承诺或替代药物诊疗请求。此类风险必须拦截 AI 自动回复，标记 requires_human_intervention=true。\n"
                "- L2 体感不适/投诉风险（标记 level='medium'）：失眠、腹痛腹泻、胃部不适、胀气恶心、心悸心慌、持续身体不适等生理体感波动。此类体感标记为 medium，需标记 requires_human_intervention=true，但不要拦截 AI 的温和指导，提示转人工复核即可。\n"
                "- L1 执行/服务敏感（标记 level='low'）：怕吃不敢吃、轻度控卡焦虑、对产品加油包等使用疑惑、普通的活动咨询、或者口头询问退款退出规则。此类标记为 low，requires_human_intervention=false，AI 可以正常应答，仅做静默标记。\n\n"
                
                "【输出 JSON 字段要求】\n"
                "- requires_human_intervention(boolean): 是否需要人工/班主任介入。只有在命中 L3（high）或 L2（medium）风险，或强烈负面情绪时才为 true。L1（low）必须为 false。\n"
                "- intervention_reasons/risk_flags(array): 风险明细，每项包含: code(字符串), level('low'|'medium'|'high'), reason(风险描述), evidence(关键原话), source(来源)。\n"
                "- renewal_intent/refund_intent(object): 续购与退款意愿识别，包含 detected(boolean), reason, evidence, source。\n"
                "- emotion_signal(object): 包含 detected(boolean), level('low'|'medium'|'high'), reason, evidence, source。仅在用户极度焦虑、崩溃、想放弃、抑郁时 detected=true。\n"
                "- confidence(number), notes(string)。\n\n"
                
                "结合 recent_user_messages 上下文进行多轮会话的合并研判。如果没有明确证据，请保持 detected=false 且数组为空，禁止强行脑补或对无害词汇（如‘稍微有点心慌咖啡因敏感’）进行过度标记。"
            ),
            user_prompt=(
                f"来源：{source}\n"
                f"待识别内容：\n{content}\n\n"
                f"上下文：\n{json.dumps(context or {}, ensure_ascii=False, indent=2)}"
            ),
        )
        return _parse_json_object(text)

    async def generate_document(
        self,
        *,
        document_type: str,
        title: str,
        answers: dict[str, Any],
        profile: dict[str, Any] | None,
    ) -> str:
        return await self.generate_text(
            system_prompt=(
                "你是燃AI交付文件撰写助手。只输出 Markdown 正文。"
                "内容要面向内部审核，不做医疗诊断或治疗承诺。"
            ),
            user_prompt=(
                f"请生成交付文件：{title}\n"
                f"document_type={document_type}\n\n"
                f"用户画像：\n{json.dumps(profile or {}, ensure_ascii=False, indent=2)}\n\n"
                f"问卷答案：\n{json.dumps(answers, ensure_ascii=False, indent=2)}"
            ),
        )

    async def close(self) -> None:
        await self._client.close()


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {"summary": text, "raw": value}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {"summary": text, "raw": value}
            except json.JSONDecodeError:
                pass
    return {"summary": text, "raw_text": text}
