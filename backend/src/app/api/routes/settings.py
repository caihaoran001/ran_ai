from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db.session import get_session
from app.services.content_policy import (
    get_ai_reply_delivery_mode,
    get_content_delivery_mode,
    set_ai_reply_delivery_mode,
    set_content_delivery_mode,
)
from app.services.knowledge_evolution_settings import (
    get_knowledge_evolution_config,
    set_knowledge_evolution_config,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class AutoSendSetting(BaseModel):
    auto_send_documents: bool


class ContentDeliveryPolicy(BaseModel):
    mode: str = "manual_review"


@router.get("/settings/auto-send")
async def get_auto_send_setting(session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    return {"auto_send_documents": await get_content_delivery_mode(session) == "auto_send"}


@router.post("/settings/auto-send")
async def update_auto_send_setting(
    payload: AutoSendSetting,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    mode = "auto_send" if payload.auto_send_documents else "manual_review"
    saved = await set_content_delivery_mode(mode, session)
    return {"auto_send_documents": saved == "auto_send"}


@router.get("/settings/content-delivery-policy", response_model=ContentDeliveryPolicy)
async def get_content_delivery_policy(session: AsyncSession = Depends(get_session)) -> ContentDeliveryPolicy:
    return ContentDeliveryPolicy(mode=await get_content_delivery_mode(session))


@router.post("/settings/content-delivery-policy", response_model=ContentDeliveryPolicy)
async def update_content_delivery_policy(
    payload: ContentDeliveryPolicy,
    session: AsyncSession = Depends(get_session),
) -> ContentDeliveryPolicy:
    return ContentDeliveryPolicy(mode=await set_content_delivery_mode(payload.mode, session))


@router.get("/settings/ai-reply-delivery-policy", response_model=ContentDeliveryPolicy)
async def get_ai_reply_delivery_policy(session: AsyncSession = Depends(get_session)) -> ContentDeliveryPolicy:
    return ContentDeliveryPolicy(mode=await get_ai_reply_delivery_mode(session))


@router.post("/settings/ai-reply-delivery-policy", response_model=ContentDeliveryPolicy)
async def update_ai_reply_delivery_policy(
    payload: ContentDeliveryPolicy,
    session: AsyncSession = Depends(get_session),
) -> ContentDeliveryPolicy:
    return ContentDeliveryPolicy(mode=await set_ai_reply_delivery_mode(payload.mode, session))


class RiskFixedReply(BaseModel):
    content: str


@router.get("/settings/risk-fixed-reply", response_model=RiskFixedReply)
async def get_risk_fixed_reply_api(session: AsyncSession = Depends(get_session)) -> RiskFixedReply:
    from app.services.content_policy import get_risk_fixed_reply
    return RiskFixedReply(content=await get_risk_fixed_reply(session))


@router.post("/settings/risk-fixed-reply", response_model=RiskFixedReply)
async def update_risk_fixed_reply_api(
    payload: RiskFixedReply,
    session: AsyncSession = Depends(get_session),
) -> RiskFixedReply:
    from app.services.content_policy import set_risk_fixed_reply
    return RiskFixedReply(content=await set_risk_fixed_reply(payload.content, session))


class KnowledgeEvolutionSetting(BaseModel):
    enabled: bool = False
    interval_seconds: int = 3600
    qa_at: str = "00:00"
    daily_qa_limit: int = 500
    case_days: str = "3,7,14,21,28"
    case_limit_per_case: int = 200
    timezone: str = "Asia/Shanghai"


@router.get("/settings/knowledge-evolution", response_model=KnowledgeEvolutionSetting)
async def get_knowledge_evolution_setting_api(
    session: AsyncSession = Depends(get_session),
) -> KnowledgeEvolutionSetting:
    config = await get_knowledge_evolution_config(session)
    return KnowledgeEvolutionSetting(**config.__dict__)


@router.post("/settings/knowledge-evolution", response_model=KnowledgeEvolutionSetting)
async def update_knowledge_evolution_setting_api(
    payload: KnowledgeEvolutionSetting,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeEvolutionSetting:
    config = await set_knowledge_evolution_config(session, payload.model_dump())
    return KnowledgeEvolutionSetting(**config.__dict__)


class QiweAccountEntry(BaseModel):
    guid: str = ""
    nickname: str = ""
    alias: str = ""
    avatar_url: str = ""
    real_name: str = ""
    last_used_at: str | None = None
    saved_at: str | None = None
    is_current: bool = False
    token_masked: str = ""
    token_set: bool = False


class QiweAccountState(BaseModel):
    current: QiweAccountEntry | None = None
    history: list[QiweAccountEntry] = []


class QiweAccountResult(BaseModel):
    state: QiweAccountState
    warning: str = ""


class QiweAccountUpdate(BaseModel):
    guid: str
    token: str


class QiweSwitchRequest(BaseModel):
    guid: str


@router.get("/settings/qiwe-account", response_model=QiweAccountState)
async def get_qiwe_account_api(session: AsyncSession = Depends(get_session)) -> QiweAccountState:
    from app.services.qiwe_account_settings import get_qiwe_account_state

    return QiweAccountState(**await get_qiwe_account_state(session))


@router.post("/settings/qiwe-account", response_model=QiweAccountResult)
async def update_qiwe_account_api(
    payload: QiweAccountUpdate,
    session: AsyncSession = Depends(get_session),
) -> QiweAccountResult:
    from fastapi import HTTPException

    from app.services.qiwe_account_settings import add_and_switch_account

    try:
        result = await add_and_switch_account(payload.guid, payload.token, session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return QiweAccountResult(**result)


@router.post("/settings/qiwe-account/switch", response_model=QiweAccountResult)
async def switch_qiwe_account_api(
    payload: QiweSwitchRequest,
    session: AsyncSession = Depends(get_session),
) -> QiweAccountResult:
    from fastapi import HTTPException

    from app.services.qiwe_account_settings import switch_to_account

    try:
        result = await switch_to_account(payload.guid, session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return QiweAccountResult(**result)


class StaffSenderRule(BaseModel):
    type: str  # "equals" or "contains"
    pattern: str


class StaffSenderSettings(BaseModel):
    rules: list[StaffSenderRule]


@router.get("/settings/qiwei-staff-senders", response_model=StaffSenderSettings)
async def get_qiwei_staff_senders_api(session: AsyncSession = Depends(get_session)) -> StaffSenderSettings:
    from app.services.content_policy import get_qiwei_staff_senders
    rules = await get_qiwei_staff_senders(session)
    return StaffSenderSettings(rules=rules)


@router.post("/settings/qiwei-staff-senders", response_model=StaffSenderSettings)
async def update_qiwei_staff_senders_api(
    payload: StaffSenderSettings,
    session: AsyncSession = Depends(get_session),
) -> StaffSenderSettings:
    from app.services.content_policy import set_qiwei_staff_senders
    rules = [rule.model_dump() for rule in payload.rules]
    saved_rules = await set_qiwei_staff_senders(rules, session)
    return StaffSenderSettings(rules=saved_rules)

