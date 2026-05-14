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
                "你是燃AI内部运营助手。基于问卷生成用户画像。"
                "输出 JSON，字段包含 summary、state、main_problem、day28_goal、risks、notes。"
            ),
            user_prompt=f"问卷答案：\n{json.dumps(answers, ensure_ascii=False, indent=2)}",
        )
        parsed = _parse_json_object(text)
        return parsed, str(parsed.get("summary") or text[:1000])

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
