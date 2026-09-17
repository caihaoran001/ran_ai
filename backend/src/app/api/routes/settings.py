from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db.session import get_session
from app.services.content_policy import (
    get_ai_reply_state,
    get_content_delivery_mode,
    set_ai_reply_enabled,
    set_content_delivery_mode,
)
from app.services.stage_report_settings import (
    get_stage_report_schedule,
    set_stage_report_schedule,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class AutoSendSetting(BaseModel):
    auto_send_documents: bool


class ContentDeliveryPolicy(BaseModel):
    mode: str = "manual_review"


class AiReplyEnabled(BaseModel):
    enabled: bool = True
    auto_disabled_reason: str | None = None
    auto_disabled_at: str | None = None


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


@router.get("/settings/ai-reply-enabled", response_model=AiReplyEnabled)
async def get_ai_reply_enabled_setting(session: AsyncSession = Depends(get_session)) -> AiReplyEnabled:
    return AiReplyEnabled(**await get_ai_reply_state(session))


@router.post("/settings/ai-reply-enabled", response_model=AiReplyEnabled)
async def update_ai_reply_enabled_setting(
    payload: AiReplyEnabled,
    session: AsyncSession = Depends(get_session),
) -> AiReplyEnabled:
    await set_ai_reply_enabled(payload.enabled, session)
    return AiReplyEnabled(**await get_ai_reply_state(session))


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


class StageReportScheduleSetting(BaseModel):
    generate_at: str = "21:00"


@router.get("/settings/stage-report-schedule", response_model=StageReportScheduleSetting)
async def get_stage_report_schedule_api(
    session: AsyncSession = Depends(get_session),
) -> StageReportScheduleSetting:
    schedule = await get_stage_report_schedule(session)
    return StageReportScheduleSetting(generate_at=schedule.generate_at)


@router.post("/settings/stage-report-schedule", response_model=StageReportScheduleSetting)
async def update_stage_report_schedule_api(
    payload: StageReportScheduleSetting,
    session: AsyncSession = Depends(get_session),
) -> StageReportScheduleSetting:
    schedule = await set_stage_report_schedule(session, payload.model_dump())
    return StageReportScheduleSetting(generate_at=schedule.generate_at)


class QiweAccountEntry(BaseModel):
    guid: str = ""
    nickname: str = ""
    alias: str = ""
    avatar_url: str = ""
    real_name: str = ""
    note: str = ""
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


class QiweNoteRequest(BaseModel):
    guid: str
    note: str = ""


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


@router.post("/settings/qiwe-account/note", response_model=QiweAccountResult)
async def set_qiwe_account_note_api(
    payload: QiweNoteRequest,
    session: AsyncSession = Depends(get_session),
) -> QiweAccountResult:
    from fastapi import HTTPException

    from app.services.qiwe_account_settings import set_account_note

    try:
        result = await set_account_note(payload.guid, payload.note, session)
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


class CuratedVideoPushSetting(BaseModel):
    enabled: bool = True
    send_at: str = "12:00"
    scope: str = "all"


@router.get("/settings/curated-video-push", response_model=CuratedVideoPushSetting)
async def get_curated_video_push_setting(
    session: AsyncSession = Depends(get_session),
) -> CuratedVideoPushSetting:
    from app.services.curated_video_settings import get_curated_video_push_settings

    return CuratedVideoPushSetting(**await get_curated_video_push_settings(session))


@router.post("/settings/curated-video-push", response_model=CuratedVideoPushSetting)
async def update_curated_video_push_setting(
    payload: CuratedVideoPushSetting,
    session: AsyncSession = Depends(get_session),
) -> CuratedVideoPushSetting:
    from app.services.curated_video_settings import set_curated_video_push_settings

    saved = await set_curated_video_push_settings(payload.model_dump(), session)
    return CuratedVideoPushSetting(**saved)


# --------------------------------------------------------------------------
# 第28天结营复盘：话术与权益配置
#
# 这两组配置都由服务层只做 flush、由路由显式 commit。刻意不在服务层 commit：
# 同一批函数还要被调度器复用在"占券发放名额 → 发消息 → 记账"的同一个事务里，
# 发送失败必须能整笔回滚，服务层自己提交就把这条路堵死了。
# --------------------------------------------------------------------------


class FinalReviewScriptOption(BaseModel):
    # key 是代码分支依赖的语义标识，前端只读展示、不给编辑；text 才是可改的文案。
    key: str
    text: str


class FinalReviewScriptQuestion(BaseModel):
    id: str
    text: str
    options: list[FinalReviewScriptOption] = []
    ask_when: str = "always"


class FinalReviewScriptNode(BaseModel):
    questions: list[FinalReviewScriptQuestion] = []


class FinalReviewScriptSetting(BaseModel):
    language: str = "zh"
    # None 表示库里还没有任何版本，当前展示的是代码种子——生产出现这个值说明
    # 种子还没同步入库，运营改了也不会影响发送。
    version_no: int | None = None
    nodes: dict[str, FinalReviewScriptNode] = {}


class FinalReviewScriptUpdate(BaseModel):
    language: str = "zh"
    nodes: dict[str, FinalReviewScriptNode]
    changed_by_name: str | None = None
    note: str | None = None


@router.get("/settings/final-review-scripts", response_model=FinalReviewScriptSetting)
async def get_final_review_scripts_api(
    language: str = Query("zh"),
    session: AsyncSession = Depends(get_session),
) -> FinalReviewScriptSetting:
    from app.services.final_review_scripts import get_active_script

    script = await get_active_script(session, language=language)
    return FinalReviewScriptSetting(
        language=script.language,
        version_no=script.version_no,
        nodes=script.nodes,
    )


@router.post("/settings/final-review-scripts", response_model=FinalReviewScriptSetting)
async def update_final_review_scripts_api(
    payload: FinalReviewScriptUpdate,
    session: AsyncSession = Depends(get_session),
) -> FinalReviewScriptSetting:
    from app.services.final_review_scripts import save_script_version

    try:
        script = await save_script_version(
            session,
            language=payload.language,
            nodes=payload.model_dump()["nodes"],
            changed_by_name=payload.changed_by_name,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return FinalReviewScriptSetting(
        language=script.language,
        version_no=script.version_no,
        nodes=script.nodes,
    )


class Day28BenefitEntry(BaseModel):
    code: str
    name: str
    kind: str = "coupon"
    face_value: str = ""
    scope: str = ""
    threshold: str = ""
    validity_days: int | None = None
    claim_url: str = ""
    enabled: bool = False
    grant_when: str = "always"
    # 只读：开着但没填领取链接就不可发，前端要能直接看出来是哪一张卡在这。
    grantable: bool = False


class Day28BenefitSetting(BaseModel):
    benefits: list[Day28BenefitEntry] = []


class Day28BenefitUpdate(BaseModel):
    benefits: list[Day28BenefitEntry]


def _benefit_setting(config) -> Day28BenefitSetting:
    return Day28BenefitSetting(
        benefits=[
            Day28BenefitEntry(**benefit.to_dict(), grantable=benefit.grantable)
            for benefit in config.benefits
        ]
    )


@router.get("/settings/day28-benefits", response_model=Day28BenefitSetting)
async def get_day28_benefits_api(
    session: AsyncSession = Depends(get_session),
) -> Day28BenefitSetting:
    from app.services.day28_benefit_settings import get_benefit_config

    try:
        config = await get_benefit_config(session)
    except ValueError as exc:
        # 库里存着一份非法配置时明说，不静默回落到默认——那会让"我明明配了券"
        # 变成"助教一个字不提"的哑谜。
        raise HTTPException(status_code=500, detail=f"券配置无法解析：{exc}") from exc
    return _benefit_setting(config)


@router.post("/settings/day28-benefits", response_model=Day28BenefitSetting)
async def update_day28_benefits_api(
    payload: Day28BenefitUpdate,
    session: AsyncSession = Depends(get_session),
) -> Day28BenefitSetting:
    from app.services.day28_benefit_settings import set_benefit_config

    try:
        config = await set_benefit_config(session, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return _benefit_setting(config)


class CheckinRewardTierEntry(BaseModel):
    days: int
    reward_text: str


class CheckinRewardTierSetting(BaseModel):
    enabled: bool = True
    program_start_date: str | None = None
    tiers: list[CheckinRewardTierEntry] = []


class CheckinRewardTierUpdate(BaseModel):
    enabled: bool = True
    tiers: list[CheckinRewardTierEntry]


def _checkin_reward_tier_setting(config) -> CheckinRewardTierSetting:
    return CheckinRewardTierSetting(
        enabled=config.enabled,
        program_start_date=(
            config.program_start_date.isoformat()
            if config.program_start_date is not None
            else None
        ),
        tiers=[CheckinRewardTierEntry(**tier.to_dict()) for tier in config.tiers],
    )


@router.get("/settings/checkin-reward-tiers", response_model=CheckinRewardTierSetting)
async def get_checkin_reward_tiers_api(
    session: AsyncSession = Depends(get_session),
) -> CheckinRewardTierSetting:
    from app.services.checkin_reward_settings import get_reward_tier_config

    try:
        config = await get_reward_tier_config(session)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"打卡奖励档位配置无法解析：{exc}") from exc
    return _checkin_reward_tier_setting(config)


@router.post("/settings/checkin-reward-tiers", response_model=CheckinRewardTierSetting)
async def update_checkin_reward_tiers_api(
    payload: CheckinRewardTierUpdate,
    session: AsyncSession = Depends(get_session),
) -> CheckinRewardTierSetting:
    from app.services.checkin_reward_settings import set_reward_tier_config

    try:
        config = await set_reward_tier_config(session, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return _checkin_reward_tier_setting(config)
