"""第28天结营复盘：话术配置、客户答案、券配置与发放台账。

这一层刻意全是确定性逻辑，不涉及模型调用——话术从库里取、该问哪题按已有答案算、
券发不发按配置和台账算。覆盖重点是几处"错了不会报错、只会静默失效"的地方：

- 话术改库不改码就要生效（本仓库在入营问卷模板上踩过：改了代码默认值，线上没变化）。
- 选项语义 key 必须稳定：运营改文案不能让"发会员券""少打扰生效"这些分支失灵。
- 客户跳过、拒答、答不清要各自成立，不能混成"没答"。
- 同一张券对同一服务单只能发一次（重复发券是直接的钱损失）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.services.archivist.graph import run_archivist
from app.services.document_prompts import _stage_report_method_prompt
from app.services.stage_report import (
    _followup_advice_requirement,
    _invalid_stage_payload_reasons,
)
from app.services.day28_benefit_grants import (
    benefits_to_grant,
    granted_codes,
    reserve_grant,
)
from app.services.day28_benefit_settings import (
    DEFAULT_BENEFITS,
    GRANT_ALWAYS,
    GRANT_CROSS_PRODUCT_INTENT,
    Day28Benefit,
    Day28BenefitConfig,
    describe_benefit,
    describe_benefits,
    get_benefit_config,
    parse_benefit_config,
    set_benefit_config,
)
from app.services.final_review_answers import (
    FinalReviewProgress,
    INTENT_CONTINUE,
    INTENT_PAUSE,
    STATUS_ANSWERED,
    STATUS_REFUSED,
    STATUS_SKIPPED,
    STATUS_UNCLEAR,
    load_progress,
    pending_questions,
    record_answer,
    should_ask,
)
from app.services.final_review_runner import (
    FINAL_REVIEW_WINDOW,
    NodePlan,
    _FOCUS_TITLE_PARTS,
    _focus_answer_values,
    deterministic_send_time,
    followup_prompt_block,
    question_fingerprint,
    ensure_benefit_links,
    final_review_active_for,
    select_node,
    should_skip_daily_checklist,
    trailing_silent_nodes,
)
from app.services.final_review_scripts import (
    ASK_INTENT_CONTINUE,
    FinalReviewScript,
    ASK_INTENT_PAUSE,
    ASK_OTHER_HEALTH_NEED,
    NODE_BENEFIT,
    NODE_GOAL,
    NODE_HANDOFF,
    NODE_RESULTS,
    Q_CONTACT_PREFERENCE,
    Q_NEXT_GOAL,
    Q_OTHER_HEALTH,
    Q_REASON_CONTINUE,
    Q_REASON_PAUSE,
    Q_BENEFIT_TOPUP,
    Q_RESULT_CHANGES,
    Q_SERVICE_INTENT,
    ensure_seeded,
    format_question_for_send,
    get_active_script,
    normalize_nodes,
    render_question_text,
    save_script_version,
    seed_nodes,
)


async def _new_case(create_case):
    response = await create_case({"group_id": f"g-{uuid4().hex[:8]}", "user": {"name": "复盘测试"}})
    return response.json()["id"]


# --------------------------------------------------------------------------
# 话术：种子、版本、校验、渲染
# --------------------------------------------------------------------------


def test_seed_scripts_pass_their_own_validation():
    """种子话术必须能过自己的校验——否则第一次保存就炸在运营手里。"""
    for language in ("zh", "en"):
        normalized = normalize_nodes(seed_nodes(language))
        assert set(normalized) == {NODE_RESULTS, NODE_GOAL, NODE_BENEFIT, NODE_HANDOFF}


def test_seed_keeps_undecided_option():
    """「还没想好」必须留着：删了等于逼客户在"继续"和"不继续"之间表态。"""
    for language in ("zh", "en"):
        nodes = seed_nodes(language)
        for question in nodes[NODE_GOAL]["questions"]:
            if question["id"] == Q_NEXT_GOAL:
                assert "undecided" in {option["key"] for option in question["options"]}


def test_seed_contact_option_says_class_teacher_not_assistant():
    """人工动作的措辞必须是「班主任」——助教是 AI，写「助教联系我」等于骗客户。"""
    nodes = seed_nodes("zh")
    contact = next(
        question
        for question in nodes[NODE_HANDOFF]["questions"]
        if question["id"] == Q_CONTACT_PREFERENCE
    )
    coach_once = next(
        option for option in contact["options"] if option["key"] == "coach_once"
    )
    assert "班主任" in coach_once["text"]
    assert "助教" not in coach_once["text"]


@pytest.mark.asyncio
async def test_script_falls_back_to_seed_when_db_empty(db_session):
    script = await get_active_script(db_session, language="zh")
    assert script.version_no is None
    assert script.questions_for(NODE_RESULTS)


@pytest.mark.asyncio
async def test_script_reads_from_db_not_code_default(db_session):
    """改库不改码就要生效。

    这是本仓库踩过的坑：入营问卷模板改了代码里的默认 JSON，线上问卷毫无变化，
    因为线上读的是数据库表。这条测试钉住"发出去的话术来自库"。
    """
    nodes = seed_nodes("zh")
    nodes[NODE_RESULTS]["questions"][0]["text"] = "运营改过的问法：这28天感觉怎么样？"
    await save_script_version(
        db_session, language="zh", nodes=nodes, changed_by_name="Lucy", note="试改"
    )

    script = await get_active_script(db_session, language="zh")
    assert script.version_no == 1
    assert script.question(Q_RESULT_CHANGES)["text"].startswith("运营改过的问法")


@pytest.mark.asyncio
async def test_saving_again_appends_a_new_version(db_session):
    """新版本是追加，旧版本还在——改坏了把旧内容再存一次就是回滚。"""
    await save_script_version(db_session, language="zh", nodes=seed_nodes("zh"))
    second = await save_script_version(
        db_session, language="zh", nodes=seed_nodes("zh"), note="第二版"
    )
    assert second.version_no == 2
    assert (await get_active_script(db_session, language="zh")).version_no == 2


@pytest.mark.asyncio
async def test_ensure_seeded_is_idempotent_and_never_overwrites(db_session):
    nodes = seed_nodes("zh")
    nodes[NODE_RESULTS]["questions"][0]["text"] = "运营自己写的问法"
    await save_script_version(db_session, language="zh", nodes=nodes)

    written = await ensure_seeded(db_session, languages=("zh", "en"))

    # 中文已有版本 → 不动；英文没有 → 写入种子。
    assert written == ["en"]
    script = await get_active_script(db_session, language="zh")
    assert script.question(Q_RESULT_CHANGES)["text"] == "运营自己写的问法"


def test_unknown_placeholder_is_rejected():
    """未知占位符会原样发到客户眼前，必须在保存时就拦住。"""
    nodes = seed_nodes("zh")
    nodes[NODE_RESULTS]["questions"][0]["text"] = "你的{体重目标}达成了吗？"
    with pytest.raises(ValueError, match="占位符"):
        normalize_nodes(nodes)


def test_open_question_cannot_carry_options():
    nodes = seed_nodes("zh")
    nodes[NODE_RESULTS]["questions"][0]["options"] = [{"key": "x", "text": "有变化"}]
    with pytest.raises(ValueError, match="开放题"):
        normalize_nodes(nodes)


def test_unknown_option_key_is_rejected():
    """新增选项必须先有代码语义，否则客户选了等于没选。"""
    nodes = seed_nodes("zh")
    goal = nodes[NODE_GOAL]["questions"][0]
    goal["options"].append({"key": "buy_more_boxes", "text": "想一次囤三盒"})
    with pytest.raises(ValueError, match="选项标识"):
        normalize_nodes(nodes)


def test_option_text_can_be_freely_rewritten():
    """文案随便改，key 不动——这是运营改文案不会弄坏代码分支的前提。"""
    nodes = seed_nodes("zh")
    intent = next(
        question
        for question in nodes[NODE_BENEFIT]["questions"]
        if question["id"] == Q_SERVICE_INTENT
    )
    for option in intent["options"]:
        if option["key"] == "continue_original":
            option["text"] = "接着用28日套装"
    # `explore_other` 已从种子里去掉（和第27天那道目标题重复），但 key 仍然合法：
    # 运营想把它加回来随时可以，加回来"发跨品会员券"那条分支照旧成立。
    intent["options"].append({"key": "explore_other", "text": "想尝尝你们别的咖啡"})

    normalized = normalize_nodes(nodes)
    rewritten = next(
        question
        for question in normalized[NODE_BENEFIT]["questions"]
        if question["id"] == Q_SERVICE_INTENT
    )
    texts = {option["key"]: option["text"] for option in rewritten["options"]}
    assert texts["continue_original"] == "接着用28日套装"
    assert texts["explore_other"] == "想尝尝你们别的咖啡"


def test_questions_with_code_branches_cannot_be_deleted():
    """删掉 R4 不会报错，只会让"发会员券"永远等不到答案——所以保存时拦住。"""
    nodes = seed_nodes("zh")
    nodes[NODE_BENEFIT]["questions"] = [
        question
        for question in nodes[NODE_BENEFIT]["questions"]
        if question["id"] != Q_SERVICE_INTENT
    ]
    with pytest.raises(ValueError, match="代码分支依赖"):
        normalize_nodes(nodes)


def test_focus_placeholder_is_filled_and_degrades_to_a_sentence():
    text = "回看这28天，你最关注的「{关注项}」分别有什么变化？"
    filled = render_question_text(text, focus_items=["腰腹围度", "睡眠", "精力"])
    assert "「腰腹围度、睡眠、精力」" in filled

    # 拿不到关注项时整句降级，不留一对空书名号。
    degraded = render_question_text(text, focus_items=[])
    assert "最在意的那几项" in degraded
    assert "「」" not in degraded
    assert "{" not in degraded


def test_focus_placeholder_degrades_in_english_too():
    text = "how did each of the things you cared most about — {focus_items} — change?"
    degraded = render_question_text(text, focus_items=[], language="en")
    assert "{" not in degraded
    assert "the things you cared most about" in degraded


def test_focus_placeholder_takes_at_most_three_items():
    filled = render_question_text(
        "你最关注的「{关注项}」", focus_items=["腰围", "睡眠", "精力", "排便"]
    )
    assert "排便" not in filled


def test_send_format_numbers_options_and_invites_a_number():
    """选项带编号发出，客户回一个数字就算答完。"""
    question = {
        "id": Q_NEXT_GOAL,
        "text": "接下来4周，你最想优先做什么？",
        "options": [
            {"key": "lose_weight", "text": "继续减重／减脂"},
            {"key": "maintain", "text": "维持现在的状态"},
        ],
    }
    assert format_question_for_send(question).splitlines() == [
        "接下来4周，你最想优先做什么？",
        "1. 继续减重／减脂",
        "2. 维持现在的状态",
        "（回编号就行，也可以直接说你的情况）",
    ]


def test_numbering_is_generated_from_current_order():
    """编号必须现生成。

    运营在后台删掉中间一项后，手写的序号会留下 1. 3. 4. 这种断号，客户回"3"
    就被静默记成另一项——所以钉住"删完仍然连续"。
    """
    question = {
        "id": Q_NEXT_GOAL,
        "text": "接下来4周，你最想优先做什么？",
        "options": [
            {"key": "lose_weight", "text": "继续减重／减脂"},
            {"key": "other_health", "text": "先改善其他健康困扰"},
            {"key": "undecided", "text": "还没想好"},
        ],
    }
    # 运营删掉中间那一项。
    question["options"] = [question["options"][0], question["options"][2]]

    lines = format_question_for_send(question).splitlines()

    assert lines[1] == "1. 继续减重／减脂"
    assert lines[2] == "2. 还没想好"


def test_open_questions_get_no_numbering_hint():
    """开放题没有选项，跟着一句"回编号就行"会让客户以为漏发了选项。"""
    rendered = format_question_for_send(
        {"id": Q_RESULT_CHANGES, "text": "这28天你最大的变化是什么？", "options": []}
    )
    assert rendered == "这28天你最大的变化是什么？"


def test_send_format_numbers_english_options():
    question = {
        "id": Q_NEXT_GOAL,
        "text": "What matters most next?",
        "options": [{"key": "maintain", "text": "Keep my current weight"}],
    }
    lines = format_question_for_send(question, language="en").splitlines()
    assert lines[1] == "1. Keep my current weight"
    assert lines[2] == "(Just reply with the number, or tell me in your own words.)"


# --------------------------------------------------------------------------
# 客户答案：状态区分、意愿分桶、该问哪题
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_answer_is_kept_as_history_and_latest_wins(db_session, create_case):
    """客户改口时两条都在——V2.0 要求"不覆盖得像从未改变"。"""
    case_id = await _new_case(create_case)
    now = datetime.now(tz=timezone.utc)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_SERVICE_INTENT,
        answer_status=STATUS_ANSWERED,
        raw_text="先用完再说",
        normalized_value={"selected": ["finish_stock"]},
        answered_at=now - timedelta(hours=2),
    )
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_SERVICE_INTENT,
        answer_status=STATUS_ANSWERED,
        raw_text="想想还是继续吧",
        normalized_value={"selected": ["continue_original"]},
        answered_at=now,
    )

    progress = await load_progress(db_session, service_case_id=case_id)
    assert progress.selected_keys(Q_SERVICE_INTENT) == ["continue_original"]
    assert progress.intent_bucket == INTENT_CONTINUE

    from sqlalchemy import func, select

    from app.models.final_review_answer import FinalReviewAnswer

    total = await db_session.scalar(
        select(func.count())
        .select_from(FinalReviewAnswer)
        .where(FinalReviewAnswer.service_case_id == case_id)
    )
    assert total == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [STATUS_SKIPPED, STATUS_REFUSED])
async def test_skipped_and_refused_count_as_resolved(db_session, create_case, status):
    """跳过和拒答都算问完了，不能反复追同一题。"""
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_NEXT_GOAL,
        answer_status=status,
        raw_text="不想说",
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert progress.is_resolved(Q_NEXT_GOAL)
    # 但它们不是有效选择，不能拿去推断意愿。
    assert progress.selected_keys(Q_NEXT_GOAL) == []


@pytest.mark.asyncio
async def test_unclear_answer_is_asked_again(db_session, create_case):
    """答了但没法归类 → 这题还没问完，下轮可以再确认一次。"""
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_NEXT_GOAL,
        answer_status=STATUS_UNCLEAR,
        raw_text="看情况吧",
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert not progress.is_resolved(Q_NEXT_GOAL)


@pytest.mark.asyncio
async def test_negative_answer_is_still_an_answer(db_session, create_case):
    """"暂时不需要后续安排"是有效答案，不是缺失——不能当成没回复。"""
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_SERVICE_INTENT,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["no_followup"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert progress.is_resolved(Q_SERVICE_INTENT)
    assert progress.intent_bucket == INTENT_PAUSE


@pytest.mark.asyncio
async def test_continue_intent_wins_over_finish_stock(db_session, create_case):
    """既想继续又说先用完库存：按继续处理（时机不同，不是不想继续）。"""
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_SERVICE_INTENT,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["finish_stock", "continue_original"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert progress.intent_bucket == INTENT_CONTINUE


@pytest.mark.asyncio
async def test_undecided_intent_asks_neither_reason_question(db_session, create_case):
    """只说"还没想好"时两套原因问法都不问——不逼客户解释一个还没做的决定。"""
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_SERVICE_INTENT,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["undecided"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert progress.intent_bucket is None

    script = await get_active_script(db_session, language="zh")
    asked = {
        question["id"]
        for question in pending_questions(script.questions_for(NODE_HANDOFF), progress)
    }
    assert Q_REASON_CONTINUE not in asked
    assert Q_REASON_PAUSE not in asked
    # 联系偏好与意愿无关，仍然要问。
    assert Q_CONTACT_PREFERENCE in asked


@pytest.mark.asyncio
async def test_reason_question_follows_the_intent_bucket(db_session, create_case):
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_SERVICE_INTENT,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["no_purchase_self"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    script = await get_active_script(db_session, language="zh")
    asked = {
        question["id"]
        for question in pending_questions(script.questions_for(NODE_HANDOFF), progress)
    }
    assert Q_REASON_PAUSE in asked
    assert Q_REASON_CONTINUE not in asked


@pytest.mark.asyncio
async def test_other_health_branch_only_when_relevant(db_session, create_case):
    case_id = await _new_case(create_case)
    progress = await load_progress(db_session, service_case_id=case_id)
    question = {"id": Q_OTHER_HEALTH, "text": "哪一项？", "ask_when": ASK_OTHER_HEALTH_NEED}

    assert not should_ask(question, progress)
    # 客户此前在群里提过其他健康困扰时也要问（V2.0：主动提及也算）。
    assert should_ask(question, progress, other_health_need_hint=True)

    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_NEXT_GOAL,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["other_health"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert should_ask(question, progress)


@pytest.mark.asyncio
async def test_resolved_question_is_not_asked_again(db_session, create_case):
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_CONTACT_PREFERENCE,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["less_contact"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    question = {"id": Q_CONTACT_PREFERENCE, "text": "怎么联系你？"}
    assert not should_ask(question, progress)


@pytest.mark.asyncio
async def test_cross_product_intent_from_either_question(db_session, create_case):
    """跨品券的触发条件：说想看别的产品，或下一阶段目标就是找日常产品。"""
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_NEXT_GOAL,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["daily_product"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert progress.cross_product_intent


@pytest.mark.asyncio
async def test_bad_answer_status_is_rejected(db_session, create_case):
    """状态只能是那四种：多一个自造状态就会让"算不算问完"的判断失准。"""
    case_id = await _new_case(create_case)
    with pytest.raises(ValueError, match="回答状态"):
        await record_answer(
            db_session,
            service_case_id=case_id,
            question_id=Q_NEXT_GOAL,
            answer_status="made_up",
        )


# --------------------------------------------------------------------------
# 券配置：可发判定、跨品条件、字段校验、对外描述
# --------------------------------------------------------------------------


def test_default_benefits_are_all_disabled():
    """默认三张券的骨架都是关闭的：等运营填真实规则，不能凭空对客户提券。"""
    assert {benefit.code for benefit in DEFAULT_BENEFITS} == {
        "product_200",
        "repurchase_gift",
        "member_fixed",
    }
    assert all(not benefit.enabled for benefit in DEFAULT_BENEFITS)
    assert all(not benefit.grantable for benefit in DEFAULT_BENEFITS)


def test_enabled_without_claim_url_is_not_grantable():
    """开着但没链接不算可用——发"你有200元券"却没入口比不发更糟。"""
    benefit = Day28Benefit(code="product_200", name="200元产品券", enabled=True)
    assert not benefit.grantable
    with_url = Day28Benefit(
        code="product_200",
        name="200元产品券",
        enabled=True,
        claim_url="https://example.com/claim",
    )
    assert with_url.grantable


def test_cross_product_coupon_needs_the_intent():
    config = Day28BenefitConfig(
        benefits=(
            Day28Benefit(
                code="product_200",
                name="200元产品券",
                enabled=True,
                claim_url="https://example.com/a",
                grant_when=GRANT_ALWAYS,
            ),
            Day28Benefit(
                code="member_fixed",
                name="定额会员券",
                enabled=True,
                claim_url="https://example.com/b",
                grant_when=GRANT_CROSS_PRODUCT_INTENT,
            ),
        )
    )
    without = {b.code for b in config.grantable_for(cross_product_intent=False)}
    with_intent = {b.code for b in config.grantable_for(cross_product_intent=True)}
    assert without == {"product_200"}
    assert with_intent == {"product_200", "member_fixed"}


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("claim_url", "example.com/claim", "http"),
        ("validity_days", 0, "有效期"),
        ("validity_days", "很久", "整数"),
        ("grant_when", "whenever", "发放条件"),
        ("kind", "voucher", "类型"),
    ],
)
def test_benefit_field_validation(field, value, match):
    payload = {
        "benefits": [
            {
                "code": "product_200",
                "name": "200元产品券",
                "kind": "coupon",
                "grant_when": GRANT_ALWAYS,
                field: value,
            }
        ]
    }
    with pytest.raises(ValueError, match=match):
        parse_benefit_config(payload)


def test_duplicate_benefit_code_is_rejected():
    payload = {
        "benefits": [
            {"code": "product_200", "name": "券一"},
            {"code": "product_200", "name": "券二"},
        ]
    }
    with pytest.raises(ValueError, match="重复"):
        parse_benefit_config(payload)


def test_describe_benefit_only_mentions_configured_fields():
    """配置缺哪项就不提哪项，绝不用"大概""左右"补。"""
    bare = Day28Benefit(
        code="product_200",
        name="200元产品券",
        enabled=True,
        claim_url="https://example.com/a",
    )
    assert describe_benefit(bare) == "200元产品券\nhttps://example.com/a"

    full = Day28Benefit(
        code="product_200",
        name="200元产品券",
        face_value="200元",
        scope="28日早C午B晚A",
        validity_days=30,
        enabled=True,
        claim_url="https://example.com/a",
    )
    described = describe_benefit(full)
    assert "200元" in described
    assert "30天内有效" in described
    assert "适用于28日早C午B晚A" in described


def test_describe_benefits_is_empty_when_nothing_grantable():
    assert describe_benefits([]) == ""


@pytest.mark.asyncio
async def test_benefit_config_round_trip(db_session):
    payload = {
        "benefits": [
            {
                "code": "product_200",
                "name": "200元产品券",
                "kind": "coupon",
                "face_value": "200元",
                "scope": "28日早C午B晚A",
                "validity_days": 30,
                "claim_url": "https://example.com/claim",
                "enabled": True,
                "grant_when": GRANT_ALWAYS,
            }
        ]
    }
    await set_benefit_config(db_session, payload)
    config = await get_benefit_config(db_session)
    benefit = config.by_code("product_200")
    assert benefit is not None
    assert benefit.validity_days == 30
    assert benefit.grantable


@pytest.mark.asyncio
async def test_unconfigured_benefits_fall_back_to_disabled_skeleton(db_session):
    config = await get_benefit_config(db_session)
    assert all(not benefit.grantable for benefit in config.benefits)


# --------------------------------------------------------------------------
# 发放台账：同一张券只发一次
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_benefit_cannot_be_granted_twice(db_session, create_case):
    """重复发券是直接的钱损失，靠数据库唯一约束拦，不靠代码先查再插。"""
    case_id = await _new_case(create_case)
    first = await reserve_grant(
        db_session, service_case_id=case_id, benefit_code="product_200"
    )
    second = await reserve_grant(
        db_session, service_case_id=case_id, benefit_code="product_200"
    )
    assert first is not None
    assert second is None
    assert await granted_codes(db_session, service_case_id=case_id) == {"product_200"}


@pytest.mark.asyncio
async def test_different_cases_get_their_own_grants(db_session, create_case):
    case_a = await _new_case(create_case)
    case_b = await _new_case(create_case)
    assert await reserve_grant(
        db_session, service_case_id=case_a, benefit_code="product_200"
    )
    assert await reserve_grant(
        db_session, service_case_id=case_b, benefit_code="product_200"
    )


@pytest.mark.asyncio
async def test_benefits_to_grant_excludes_already_sent(db_session, create_case):
    case_id = await _new_case(create_case)
    config = Day28BenefitConfig(
        benefits=(
            Day28Benefit(
                code="product_200",
                name="200元产品券",
                enabled=True,
                claim_url="https://example.com/a",
            ),
            Day28Benefit(
                code="repurchase_gift",
                name="复购赠品券",
                enabled=True,
                claim_url="https://example.com/b",
            ),
        )
    )
    await reserve_grant(db_session, service_case_id=case_id, benefit_code="product_200")

    pending = await benefits_to_grant(
        db_session,
        service_case_id=case_id,
        config=config,
        cross_product_intent=False,
    )
    assert [benefit.code for benefit in pending] == ["repurchase_gift"]


# --------------------------------------------------------------------------
# 后台 API：话术与券配置
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_script_api_returns_seed_before_anything_is_saved(admin_client):
    response = await admin_client.get("/settings/final-review-scripts")
    assert response.status_code == 200
    body = response.json()
    # version_no 为 null 就是"库里还没有版本、现在看的是代码种子"的信号：
    # 生产上出现它说明种子没同步入库，运营改了也不影响发送。
    assert body["version_no"] is None
    assert body["language"] == "zh"
    assert body["nodes"]["results"]["questions"]


@pytest.mark.asyncio
async def test_script_api_saves_and_reads_back(admin_client):
    nodes = seed_nodes("zh")
    nodes[NODE_RESULTS]["questions"][0]["text"] = "Lucy 改过的问法：这28天怎么样？"

    saved = await admin_client.post(
        "/settings/final-review-scripts",
        json={"language": "zh", "nodes": nodes, "changed_by_name": "Lucy", "note": "试改"},
    )
    assert saved.status_code == 200
    assert saved.json()["version_no"] == 1

    reread = await admin_client.get("/settings/final-review-scripts")
    first = reread.json()["nodes"]["results"]["questions"][0]
    assert first["text"] == "Lucy 改过的问法：这28天怎么样？"


@pytest.mark.asyncio
async def test_script_api_rejects_unknown_option_key(admin_client):
    nodes = seed_nodes("zh")
    nodes[NODE_GOAL]["questions"][0]["options"].append(
        {"key": "buy_three_boxes", "text": "想一次囤三盒"}
    )
    response = await admin_client.post(
        "/settings/final-review-scripts", json={"language": "zh", "nodes": nodes}
    )
    assert response.status_code == 400
    assert "选项标识" in response.json()["detail"]


@pytest.mark.asyncio
async def test_script_api_rejects_deleting_a_branch_question(admin_client):
    nodes = seed_nodes("zh")
    nodes[NODE_HANDOFF]["questions"] = [
        question
        for question in nodes[NODE_HANDOFF]["questions"]
        if question["id"] != Q_CONTACT_PREFERENCE
    ]
    response = await admin_client.post(
        "/settings/final-review-scripts", json={"language": "zh", "nodes": nodes}
    )
    assert response.status_code == 400
    assert "代码分支依赖" in response.json()["detail"]


@pytest.mark.asyncio
async def test_benefit_api_defaults_are_disabled_and_not_grantable(admin_client):
    response = await admin_client.get("/settings/day28-benefits")
    assert response.status_code == 200
    benefits = response.json()["benefits"]
    assert {item["code"] for item in benefits} == {
        "product_200",
        "repurchase_gift",
        "member_fixed",
    }
    assert all(not item["grantable"] for item in benefits)


@pytest.mark.asyncio
async def test_benefit_api_marks_missing_link_as_not_grantable(admin_client):
    """开着但没填链接：接口要把 grantable=false 亮出来，运营才知道卡在哪。"""
    payload = {
        "benefits": [
            {
                "code": "product_200",
                "name": "200元产品券",
                "kind": "coupon",
                "face_value": "200元",
                "enabled": True,
                "claim_url": "",
                "grant_when": "always",
            }
        ]
    }
    saved = await admin_client.post("/settings/day28-benefits", json=payload)
    assert saved.status_code == 200
    entry = saved.json()["benefits"][0]
    assert entry["enabled"] is True
    assert entry["grantable"] is False


@pytest.mark.asyncio
async def test_benefit_api_rejects_a_link_without_scheme(admin_client):
    payload = {
        "benefits": [
            {
                "code": "product_200",
                "name": "200元产品券",
                "claim_url": "example.com/claim",
            }
        ]
    }
    response = await admin_client.post("/settings/day28-benefits", json=payload)
    assert response.status_code == 400
    assert "领取入口" in response.json()["detail"]


@pytest.mark.asyncio
async def test_benefit_api_accepts_a_wechat_store_voucher_code(admin_client):
    """真实的优惠券入口是微信小店口令，不是网址——2026-09-05 上线当天被它挡住过。"""
    payload = {
        "benefits": [
            {
                "code": "member_fixed",
                "name": "40元会员券",
                "claim_url": "#微信小店://Cephei奢啡加速燃/优惠券/ollsuN9mwGhdrZl",
                "enabled": True,
                "grant_when": "cross_product_intent",
            }
        ]
    }
    response = await admin_client.post("/settings/day28-benefits", json=payload)
    assert response.status_code == 200
    entry = response.json()["benefits"][0]
    assert entry["claim_url"].startswith("#微信小店://")
    assert entry["grantable"] is True


@pytest.mark.asyncio
async def test_benefit_name_can_be_renamed(admin_client):
    """券名要能改：第一张实际做成了 240 元，名字还叫"200元产品券"就会被念错。"""
    payload = {
        "benefits": [
            {
                "code": "product_200",
                "name": "240元复购券",
                "face_value": "240元",
                "claim_url": "#微信小店://Cephei奢啡加速燃/优惠券/kVQsb47P9dnOkEA",
                "enabled": True,
            }
        ]
    }
    saved = await admin_client.post("/settings/day28-benefits", json=payload)
    assert saved.status_code == 200
    assert saved.json()["benefits"][0]["name"] == "240元复购券"


# --------------------------------------------------------------------------
# 发放台账接口
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grant_ledger_api_is_empty_before_anything_is_sent(admin_client):
    response = await admin_client.get("/day28-benefit-grants")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_grant_ledger_api_groups_by_case_and_names_the_benefit(
    admin_client, db_session, create_case
):
    """台账按服务单归组，券名取自当前配置。"""
    case_id = await _new_case(create_case)
    await set_benefit_config(
        db_session,
        {
            "benefits": [
                {
                    "code": "product_200",
                    "name": "200元产品券",
                    "claim_url": "https://example.com/claim",
                    "enabled": True,
                }
            ]
        },
    )
    await reserve_grant(db_session, service_case_id=case_id, benefit_code="product_200")
    await db_session.commit()

    response = await admin_client.get("/day28-benefit-grants")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["service_case_id"] == case_id
    assert [item["benefit_name"] for item in rows[0]["grants"]] == ["200元产品券"]
    assert rows[0]["grants"][0]["status"] == "link_sent"


@pytest.mark.asyncio
async def test_grant_ledger_api_falls_back_to_the_code_when_the_benefit_is_gone(
    admin_client, db_session, create_case
):
    """券配置被删掉后，已发出的记录不能从台账里消失。"""
    case_id = await _new_case(create_case)
    await reserve_grant(db_session, service_case_id=case_id, benefit_code="retired_coupon")
    await db_session.commit()

    rows = (await admin_client.get("/day28-benefit-grants")).json()
    assert rows[0]["grants"][0]["benefit_name"] == "retired_coupon"


# --------------------------------------------------------------------------
# 节点调度：哪天问哪个节点、什么时候停
# --------------------------------------------------------------------------


def _seed_script(language: str = "zh") -> FinalReviewScript:
    return FinalReviewScript(
        language=language, version_no=1, nodes=seed_nodes(language)
    )


def _progress(**answers) -> FinalReviewProgress:
    rows = {}
    for question_id, (status, selected) in answers.items():
        rows[question_id] = SimpleNamespace(
            question_id=question_id,
            answer_status=status,
            normalized_value={"selected": selected} if selected else None,
            raw_text="",
        )
    return FinalReviewProgress(answers=rows)


@pytest.mark.parametrize(
    "day,expected",
    [
        (25, None),
        (26, NODE_RESULTS),
        (27, NODE_RESULTS),  # 第26天没发出去（功能刚开），先补最早那个，一天只推一个
        (36, None),  # 过了最后一天不再追
    ],
)
def test_node_selection_follows_the_service_day(day, expected):
    assert (
        select_node(_seed_script(), day=day, asked_nodes=[], progress=_progress())
        == expected
    )


def test_only_one_node_advances_per_day():
    """功能中途开启时三个节点同时到期，也只能推进一个——不能当天连问四轮。"""
    script = _seed_script()
    first = select_node(script, day=30, asked_nodes=[], progress=_progress())
    second = select_node(
        script, day=30, asked_nodes=[first], progress=_progress()
    )
    assert first == NODE_RESULTS
    assert second == NODE_GOAL


def test_handoff_waits_for_the_service_intent_answer():
    """承接必须建立在客户表达过的意愿上，没答就不问。"""
    script = _seed_script()
    asked = [NODE_RESULTS, NODE_GOAL, NODE_BENEFIT]
    silent = _progress(**{Q_RESULT_CHANGES: (STATUS_ANSWERED, [])})
    assert select_node(script, day=30, asked_nodes=asked, progress=silent) is None

    answered = _progress(
        **{
            Q_RESULT_CHANGES: (STATUS_ANSWERED, []),
            Q_SERVICE_INTENT: (STATUS_ANSWERED, ["continue_original"]),
        }
    )
    assert (
        select_node(script, day=30, asked_nodes=asked, progress=answered)
        == NODE_HANDOFF
    )


def test_two_silent_nodes_stop_the_whole_round():
    """连着两个节点一个字没回就整轮停止，不再继续追问。"""
    script = _seed_script()
    silent = _progress()
    assert (
        trailing_silent_nodes(
            script, asked_nodes=[NODE_RESULTS, NODE_GOAL], progress=silent
        )
        == 2
    )
    assert (
        select_node(
            script, day=29, asked_nodes=[NODE_RESULTS, NODE_GOAL], progress=silent
        )
        is None
    )


def test_a_late_answer_resumes_the_round():
    """迟到的回答要能解除暂停：第一个节点回了，就还没到"连续两个都没回"。"""
    script = _seed_script()
    progress = _progress(**{Q_RESULT_CHANGES: (STATUS_ANSWERED, [])})
    assert (
        trailing_silent_nodes(
            script, asked_nodes=[NODE_RESULTS, NODE_GOAL], progress=progress
        )
        == 1
    )
    assert (
        select_node(
            script, day=29, asked_nodes=[NODE_RESULTS, NODE_GOAL], progress=progress
        )
        == NODE_BENEFIT
    )


def test_refused_answer_still_counts_as_an_answer_for_pausing():
    """明确说'不想聊'是回应，不是沉默——不能因此把他判成失联。"""
    script = _seed_script()
    progress = _progress(**{Q_RESULT_CHANGES: (STATUS_REFUSED, [])})
    assert (
        trailing_silent_nodes(script, asked_nodes=[NODE_RESULTS], progress=progress)
        == 0
    )


def test_send_window_is_staggered_within_the_hour():
    """同一服务单每天固定时刻、不同服务单错峰，避免整点集中发送。"""
    day = date(2026, 9, 20)
    times = {deterministic_send_time(case_id, day, NODE_RESULTS) for case_id in range(1, 40)}
    assert len(times) > 1
    assert deterministic_send_time(7, day, NODE_RESULTS) == deterministic_send_time(
        7, day, NODE_RESULTS
    )
    start, end = FINAL_REVIEW_WINDOW
    assert all(start <= item < end for item in times)


def test_whitelist_is_required_even_when_the_switch_is_on():
    """开关开了但白名单为空 = 一单都不跑；误配不能变成全量群发券。"""
    settings = SimpleNamespace(final_review_enabled=True, final_review_case_ids="")
    assert final_review_active_for(settings, 534) is False
    settings = SimpleNamespace(final_review_enabled=True, final_review_case_ids="534, 999")
    assert final_review_active_for(settings, 534) is True
    assert final_review_active_for(settings, 1) is False
    off = SimpleNamespace(final_review_enabled=False, final_review_case_ids="534")
    assert final_review_active_for(off, 534) is False


def test_daily_checklist_yields_only_on_the_review_days():
    settings = SimpleNamespace(final_review_enabled=True, final_review_case_ids="534")
    assert should_skip_daily_checklist(settings, service_case_id=534, day=27) is True
    assert should_skip_daily_checklist(settings, service_case_id=534, day=25) is False
    assert should_skip_daily_checklist(settings, service_case_id=1, day=27) is False


def test_benefit_links_are_appended_when_the_model_drops_them():
    """模型漏掉链接就由代码补回：账上记了已发券，客户手里必须真有链接。"""
    benefit = Day28Benefit(
        code="product_200",
        name="200元产品券",
        face_value="200元",
        claim_url="https://example.com/claim",
        enabled=True,
    )
    plan = NodePlan(
        node=NODE_BENEFIT,
        text="",
        reserved_benefits=[benefit],
        reserved_grants=[],
        benefits_text="",
    )
    dropped = ensure_benefit_links("你的权益已经可以领啦～", plan)
    assert "https://example.com/claim" in dropped
    kept = ensure_benefit_links(
        "权益：200元产品券 https://example.com/claim", plan
    )
    assert kept.count("https://example.com/claim") == 1


# --------------------------------------------------------------------------
# 档案官侧：答案落库与联系偏好生效
# --------------------------------------------------------------------------


class _FakeReportingModel:
    """假模型：bind_tools 返回自身，ainvoke 吐一条预设的 report tool_call。"""

    def __init__(self, tool_calls):
        self._tool_calls = tool_calls
        self.bound_tool_names: list[str] = []
        self.prompts: list[str] = []

    def bind_tools(self, tools):
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages):
        self.prompts.append(str(messages[-1].content))
        return AIMessage(content="", tool_calls=list(self._tool_calls))


async def _archivist_case(db_session, monkeypatch, *, whitelist: bool = True):
    from app.core.config import get_settings
    from app.models.archivist_progress import ArchivistProgress
    from app.models.reminder import ReminderEvent, ReminderEventType
    from app.models.service_case import ServiceCase, ServiceCaseStatus
    from app.models.user import User

    settings = get_settings()
    monkeypatch.setattr(settings, "final_review_enabled", True, raising=False)
    user = User(name="结营复盘测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-FR-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"fr-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=28),
    )
    db_session.add(case)
    await db_session.flush()
    monkeypatch.setattr(
        settings,
        "final_review_case_ids",
        str(case.id) if whitelist else "",
        raising=False,
    )
    # 已发出 benefit 节点：只有问出口的题才允许被"回答"。
    db_session.add(
        ReminderEvent(
            service_case_id=case.id,
            rule_id="final_review:benefit",
            event_date=datetime.now(timezone.utc).date(),
            event_type=ReminderEventType.REMINDED,
        )
    )
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="继续用原来的方案吧，另外别再给我推荐产品了",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    progress = ArchivistProgress(service_case_id=case.id, last_archived_message_id=0)
    db_session.add(progress)
    await db_session.flush()
    return case, message, progress


@pytest.mark.asyncio
async def test_archivist_records_a_final_review_answer(db_session, monkeypatch):
    case, message, progress = await _archivist_case(db_session, monkeypatch)
    fake = _FakeReportingModel(
        [
            {
                "name": "report_final_review_answer",
                "args": {
                    "question_id": Q_SERVICE_INTENT,
                    "answer_status": STATUS_ANSWERED,
                    "raw_text": "继续用原来的方案吧",
                    "option_keys": ["continue_original"],
                    "evidence_message_id": message.id,
                },
                "id": "call_1",
            }
        ]
    )

    await run_archivist(
        db_session, service_case=case, progress=progress, chat_model=fake
    )
    await db_session.flush()

    stored = await load_progress(db_session, service_case_id=case.id)
    assert stored.selected_keys(Q_SERVICE_INTENT) == ["continue_original"]
    assert stored.intent_bucket == INTENT_CONTINUE
    # 待答清单确实注入了，模型才可能对上题号。
    assert "final_review_pending" in fake.prompts[-1]
    # 选项编号必须跟着清单一起给：客户隔天才回一句"1"时，提问原文已归档、
    # 不在这批增量里，只剩这份清单能把编号对回选项。
    assert '"number": 1' in fake.prompts[-1]
    assert '"number": 2' in fake.prompts[-1]


@pytest.mark.asyncio
async def test_archivist_drops_an_answer_to_a_question_never_asked(
    db_session, monkeypatch
):
    """没问出口的题不许"被回答"——否则闲聊里顺嘴一句会被当成正式表态去发券。"""
    case, message, progress = await _archivist_case(db_session, monkeypatch)
    fake = _FakeReportingModel(
        [
            {
                "name": "report_final_review_answer",
                "args": {
                    "question_id": Q_CONTACT_PREFERENCE,  # handoff 节点还没发
                    "answer_status": STATUS_ANSWERED,
                    "raw_text": "少打扰我",
                    "option_keys": ["less_contact"],
                    "evidence_message_id": message.id,
                },
                "id": "call_1",
            }
        ]
    )

    await run_archivist(
        db_session, service_case=case, progress=progress, chat_model=fake
    )
    await db_session.flush()

    stored = await load_progress(db_session, service_case_id=case.id)
    assert stored.status(Q_CONTACT_PREFERENCE) is None


@pytest.mark.asyncio
async def test_final_review_context_is_not_injected_when_case_is_not_whitelisted(
    db_session, monkeypatch
):
    case, message, progress = await _archivist_case(
        db_session, monkeypatch, whitelist=False
    )
    fake = _FakeReportingModel([])

    await run_archivist(
        db_session, service_case=case, progress=progress, chat_model=fake
    )

    assert "final_review_pending" not in fake.prompts[-1]
    assert "report_final_review_answer" in fake.bound_tool_names


@pytest.mark.asyncio
async def test_contact_preference_turns_off_proactive_contact(db_session, monkeypatch):
    """R6 说了"暂停主动联系"就要真停：问了不执行比不问更伤。"""
    from app.services.checkin_followup_preferences import (
        get_followup_preference_states,
    )
    from app.models.reminder import ReminderEvent, ReminderEventType

    case, message, progress = await _archivist_case(db_session, monkeypatch)
    db_session.add(
        ReminderEvent(
            service_case_id=case.id,
            rule_id="final_review:handoff",
            event_date=datetime.now(timezone.utc).date(),
            event_type=ReminderEventType.REMINDED,
        )
    )
    await db_session.flush()
    fake = _FakeReportingModel(
        [
            {
                "name": "report_final_review_answer",
                "args": {
                    "question_id": Q_CONTACT_PREFERENCE,
                    "answer_status": STATUS_ANSWERED,
                    "raw_text": "暂停主动联系，我需要时再来找你",
                    "option_keys": ["pause_contact"],
                    "evidence_message_id": message.id,
                },
                "id": "call_1",
            }
        ]
    )

    await run_archivist(
        db_session, service_case=case, progress=progress, chat_model=fake
    )
    await db_session.flush()

    states = await get_followup_preference_states(db_session, service_case_id=case.id)
    assert states["proactive_contact"] is False
    assert states["product_recommendation"] is False
    # 风险相关提醒不走追问链路，这里不该被牵连关掉。
    assert states["contradiction"] is True


# --------------------------------------------------------------------------
# 第28天报告：分支措辞与商业词闸门
# --------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["zh", "en"])
def test_day28_report_may_extend_to_the_next_stage(language):
    """第28天营期结束，报告要能接上"下一阶段想做什么"；之前的阶段仍只写周期内。"""
    day21 = _followup_advice_requirement("day21", language=language)
    day28 = _followup_advice_requirement("day28", language=language)
    if language == "zh":
        assert "只写当前28天服务内" in day21
        assert "下一阶段" in day28
        assert "维持本身就是有效目标" in day28
        assert "待确认" in day28
    else:
        assert "within the" in day21
        assert "next-stage" in day28
        assert "Maintaining is a legitimate goal" in day28
        assert "to be confirmed" in day28


@pytest.mark.parametrize("language", ["zh", "en"])
def test_day28_report_still_forbids_commercial_wording(language):
    """允许写下一阶段 ≠ 允许卖东西：券和购买入口一律走独立消息。"""
    day28 = _followup_advice_requirement("day28", language=language)
    method = _stage_report_method_prompt("day28", language=language)
    if language == "zh":
        assert "严禁出现复购" in day28
        assert "不写券额" in day28
        assert "不写券额、赠品、购买入口" in method
    else:
        assert "strictly forbid repurchase" in day28.lower()
        assert "never coupon values" in method


def test_commercial_term_gate_is_unchanged():
    """闸门不动：第28天的下一阶段建议全是健康语言，出现"优惠"仍要被拦回。"""
    reasons = _invalid_stage_payload_reasons(
        "day28",
        {
            "stage_summary": "执行稳定",
            "risk_reminder": "无明显风险",
            "adjustment_advice": "晚餐提前一小时",
            "followup_advice": "下一阶段继续维持，另外这次有优惠可以复购",
        },
    )
    assert any("commercial" in reason for reason in reasons)


@pytest.mark.asyncio
async def test_coach_once_notifies_operations_only_once(
    db_session, monkeypatch
):
    """「请班主任联系我一次」只授权一次：发一条内部提示，不转成持续跟进。"""
    import app.services.reminder_service as reminder_service
    from app.core.config import get_settings
    from app.models.reminder import ReminderEvent, ReminderEventType

    sent: list[tuple[str, str]] = []

    async def fake_send(chat_id, content):
        sent.append((chat_id, content))
        return SimpleNamespace(success=True, message_id="m1", error=None)

    monkeypatch.setattr(
        reminder_service, "_send_channel_text_with_retries", fake_send
    )
    monkeypatch.setattr(
        get_settings(), "ai_balance_alert_group_id", "ops-group", raising=False
    )

    case, message, progress = await _archivist_case(db_session, monkeypatch)
    db_session.add(
        ReminderEvent(
            service_case_id=case.id,
            rule_id="final_review:handoff",
            event_date=datetime.now(timezone.utc).date(),
            event_type=ReminderEventType.REMINDED,
        )
    )
    await db_session.flush()
    await db_session.commit()

    await reminder_service.alert_final_review_coach_request(case.id)
    await reminder_service.alert_final_review_coach_request(case.id)

    assert len(sent) == 1
    assert sent[0][0] == "ops-group"
    assert "班主任联系一次" in sent[0][1]
    assert "不需要转为持续跟进" in sent[0][1]


def _focus_field(title: str) -> dict:
    """入营那道多选题的模板片段（中英同 key、标题不同）。"""
    return {
        "key": "CHECKBOX_FOCUS",
        "title": title,
        "type": "CHECKBOX",
        "options": [
            "白天精力更稳，忙一天也不容易垮",
            "每天节律更稳定、情绪更稳",
            "其他",
        ],
    }


def test_focus_items_keep_the_customer_own_wording():
    """关注项按客户当时勾的原话念，多选按 `||` 拆、顿号不当分隔符。"""
    fields = [_focus_field("您最希望 28 天后看到哪些变化？")]
    answers = {"CHECKBOX_FOCUS": "白天精力更稳，忙一天也不容易垮||每天节律更稳定、情绪更稳"}

    assert _focus_answer_values(fields, answers, ("最希望", "28")) == [
        "白天精力更稳，忙一天也不容易垮",
        "每天节律更稳定、情绪更稳",
    ]


def test_focus_items_do_not_collapse_unmatched_answers():
    """回归 2026-09-05 灰度事故：英文问卷的答案对不上中文选项表，被统计口径
    压成「其他（用户补充）」念进了群。复盘只念原话，不做统计折叠。"""
    fields = [_focus_field("您最希望 28 天后看到哪些变化？")]
    answers = {
        "CHECKBOX_FOCUS": (
            "More stable daytime energy, without crashing after a busy day"
            "||More stable daily rhythm and emotional state"
        )
    }

    values = _focus_answer_values(fields, answers, ("最希望", "28"))

    assert values == [
        "More stable daytime energy, without crashing after a busy day",
        "More stable daily rhythm and emotional state",
    ]
    assert not any("其他（用户补充）" in value for value in values)


def test_focus_items_drop_empty_other_and_truncate_long_free_text():
    """只勾"其他"没写内容念出来是废话，丢掉；自填过长会撑散题干，截断。"""
    fields = [_focus_field("您最希望 28 天后看到哪些变化？")]
    long_text = "想" * 200
    answers = {"CHECKBOX_FOCUS": ["其他", long_text, "其他"]}

    values = _focus_answer_values(fields, answers, ("最希望", "28"))

    assert len(values) == 1
    assert len(values[0]) == 120
    assert values[0].endswith("…")


def test_focus_title_parts_match_both_language_templates():
    """英文模板的标题是另一句话，必须也能命中，否则英文客户整句降级成没有关注项。"""
    answers = {"CHECKBOX_FOCUS": "每天节律更稳定、情绪更稳"}
    for title in (
        "您最希望 28 天后看到哪些变化？",
        "What changes do you most hope to see after 28 days?",
    ):
        fields = [_focus_field(title)]
        matched = [
            values
            for parts in _FOCUS_TITLE_PARTS
            if (values := _focus_answer_values(fields, answers, parts))
        ]
        assert matched, f"标题匹配不到：{title}"


# --------------------------------------------------------------------------
# 券的答疑工具：只能谈规则，不能给入口
# --------------------------------------------------------------------------


_LIVE_COUPON = {
    "code": "product_200",
    "name": "240元复购券",
    "kind": "coupon",
    "face_value": "240元",
    "scope": "高燃人生早C午B晚A28日套装",
    "threshold": "满600-240",
    "validity_days": 5,
    "claim_url": "#微信小店://Cephei奢啡加速燃/优惠券/kVQsb47P9dnOkEA",
    "enabled": True,
    "grant_when": GRANT_ALWAYS,
}


def test_benefit_description_without_the_claim_entry_keeps_every_rule():
    """答疑用的写法：面额、有效期、适用范围、门槛都在，入口没有。"""
    benefit = Day28Benefit(**_LIVE_COUPON)

    answerable = describe_benefit(benefit, include_claim_url=False)

    assert answerable == "240元复购券（240元，5天内有效，适用于高燃人生早C午B晚A28日套装，满600-240）"
    assert "://" not in answerable
    # 权益消息那条路径照旧带完整入口，别把它一起弄坏。
    assert _LIVE_COUPON["claim_url"] in describe_benefit(benefit)


def test_english_benefit_description_without_the_claim_entry():
    benefit = Day28Benefit(**{**_LIVE_COUPON, "name": "Repurchase coupon"})
    answerable = describe_benefit(benefit, language="en", include_claim_url=False)
    assert answerable.startswith("Repurchase coupon (")
    assert "://" not in answerable


@pytest.mark.asyncio
async def test_benefit_tool_never_hands_out_a_claim_entry(db_session, create_case):
    """回归 2026-09-07 灰度事故。

    这个工具每轮普通聊天都挂在助教手上，纪律却只在它读技能文件时才看得见。
    当时它顺手查了券、把入口提前两天念进群：账上没记这次发放（权益节点之后
    还会再发一次），链接开头的井号还被抄丢了。所以入口不许从这里出去。
    """
    from app.services.agent.state import AgentRunContext
    from app.services.agent.tools.final_review_tools import build_final_review_tools

    case_id = await _new_case(create_case)
    await set_benefit_config(db_session, {"benefits": [_LIVE_COUPON]})
    await db_session.flush()

    tool = next(
        item
        for item in build_final_review_tools(
            context=AgentRunContext(service_case_id=case_id)
        )
        if item.name == "get_day28_benefits"
    )
    result = await tool.coroutine()

    payload = json_dumps_for_assert(result)
    assert result["ok"] is True
    assert "://" not in payload
    assert "kVQsb47P9dnOkEA" not in payload
    # 规则照旧答得出来。
    assert "240元" in payload and "满600-240" in payload
    assert result["already_sent"] == []


@pytest.mark.asyncio
async def test_benefit_tool_can_still_answer_about_a_coupon_already_sent(
    db_session, create_case
):
    """发出去之后客户必然追问"怎么用、什么时候过期"，那时也得答得上。"""
    from app.services.agent.state import AgentRunContext
    from app.services.agent.tools.final_review_tools import build_final_review_tools

    case_id = await _new_case(create_case)
    await set_benefit_config(db_session, {"benefits": [_LIVE_COUPON]})
    await reserve_grant(
        db_session,
        service_case_id=case_id,
        benefit_code=_LIVE_COUPON["code"],
    )
    await db_session.flush()

    tool = next(
        item
        for item in build_final_review_tools(
            context=AgentRunContext(service_case_id=case_id)
        )
        if item.name == "get_day28_benefits"
    )
    result = await tool.coroutine()

    assert [item["code"] for item in result["already_sent"]] == ["product_200"]
    # 已发过的券不再列进"还没告知"那一组，否则会被重复告知。
    assert result["benefits"] == []
    assert "://" not in json_dumps_for_assert(result)


def json_dumps_for_assert(payload) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------
# 追问题当场问：注入回复提示词
# --------------------------------------------------------------------------


async def _sent(db_session, case, text: str) -> None:
    """模拟一条已经发到群里的助教消息。"""
    db_session.add(
        Message(
            service_case_id=case.id,
            group_id=case.group_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.SYSTEM,
            message_type="text",
            content=text,
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.SENT,
        )
    )
    await db_session.flush()


def test_question_fingerprint_skips_the_placeholder():
    """占位符渲染后每位客户都不同，指纹只能取占位符之外的原文。"""
    text = "回看这28天，你最关注的「{关注项}」分别有什么变化？哪一点对你最有价值？"
    fingerprint = question_fingerprint(text)
    assert "{" not in fingerprint and "关注项" not in fingerprint
    # 指纹必须是题干里的一段原文，否则匹配发出去的消息时对不上。
    assert fingerprint and fingerprint in text


@pytest.mark.asyncio
async def test_followup_is_injected_once_the_trigger_question_went_out(
    db_session, monkeypatch
):
    """回归 2026-09-07 的漏问。

    那道"想改善哪方面"的条件要等答案入库才成立，而它所属的节点当天就关门了，
    结果一次也没问出去、还不报错。现在只要目标题问出去了，它就进提示词，
    客户回一个"3"助教当场就能接着问。
    """
    from app.core.config import get_settings

    case, _, _ = await _archivist_case(db_session, monkeypatch)
    script = await get_active_script(db_session, language="zh")
    await _sent(db_session, case, format_question_for_send(script.question(Q_NEXT_GOAL)))

    block = await followup_prompt_block(
        db_session, settings=get_settings(), service_case=case, language="zh"
    )

    assert "想改善哪一项" in block or "改善哪" in block
    assert "仅当客户表示想先改善其他健康困扰时问这道" in block


@pytest.mark.asyncio
async def test_nothing_is_injected_before_the_trigger_question_went_out(
    db_session, monkeypatch
):
    """目标题还没问出口，这道追问题无从谈起。"""
    from app.core.config import get_settings

    case, _, _ = await _archivist_case(db_session, monkeypatch)
    block = await followup_prompt_block(
        db_session, settings=get_settings(), service_case=case, language="zh"
    )
    assert block == ""


@pytest.mark.asyncio
async def test_followup_stops_once_it_has_been_asked(db_session, monkeypatch):
    """问过就不再注入——客户没答不代表要再问一遍，问两遍比不问更像没在听。"""
    from app.core.config import get_settings

    case, _, _ = await _archivist_case(db_session, monkeypatch)
    script = await get_active_script(db_session, language="zh")
    await _sent(db_session, case, format_question_for_send(script.question(Q_NEXT_GOAL)))
    await _sent(
        db_session, case, format_question_for_send(script.question(Q_OTHER_HEALTH))
    )

    block = await followup_prompt_block(
        db_session, settings=get_settings(), service_case=case, language="zh"
    )
    assert "改善哪" not in block


@pytest.mark.asyncio
async def test_followup_stops_once_answered(db_session, monkeypatch):
    from app.core.config import get_settings

    case, message, _ = await _archivist_case(db_session, monkeypatch)
    script = await get_active_script(db_session, language="zh")
    await _sent(db_session, case, format_question_for_send(script.question(Q_NEXT_GOAL)))
    await record_answer(
        db_session,
        service_case_id=case.id,
        question_id=Q_OTHER_HEALTH,
        answer_status=STATUS_ANSWERED,
        raw_text="睡眠",
        normalized_value={"selected": ["sleep"]},
        evidence_message_id=message.id,
    )
    await db_session.flush()

    block = await followup_prompt_block(
        db_session, settings=get_settings(), service_case=case, language="zh"
    )
    assert "改善哪" not in block


@pytest.mark.asyncio
async def test_followup_is_never_injected_outside_the_whitelist(
    db_session, monkeypatch
):
    from app.core.config import get_settings

    case, _, _ = await _archivist_case(db_session, monkeypatch, whitelist=False)
    script = await get_active_script(db_session, language="zh")
    await _sent(db_session, case, format_question_for_send(script.question(Q_NEXT_GOAL)))

    block = await followup_prompt_block(
        db_session, settings=get_settings(), service_case=case, language="zh"
    )
    assert block == ""


@pytest.mark.asyncio
async def test_both_reason_variants_are_offered_with_their_condition(
    db_session, monkeypatch
):
    """意愿题问出口后，两套原因题都给它，附各自的条件——问错方向比不问更伤人。"""
    from app.core.config import get_settings

    case, _, _ = await _archivist_case(db_session, monkeypatch)
    script = await get_active_script(db_session, language="zh")
    await _sent(
        db_session, case, format_question_for_send(script.question(Q_SERVICE_INTENT))
    )

    block = await followup_prompt_block(
        db_session, settings=get_settings(), service_case=case, language="zh"
    )
    assert "仅当客户倾向继续原方案、或想看其他产品时问这道" in block
    assert "仅当客户倾向先用完现有的、暂不购买或不需要后续时问这道" in block


def test_other_health_question_is_now_a_numbered_choice():
    """原稿把五项写在题干里当开放题问；改成选项后客户回一个编号就算答完。"""
    for language in ("zh", "en"):
        nodes = normalize_nodes(seed_nodes(language))
        question = next(
            item
            for item in nodes[NODE_GOAL]["questions"]
            if item["id"] == Q_OTHER_HEALTH
        )
        assert [option["key"] for option in question["options"]] == [
            "digestion",
            "sleep",
            "less_sugar",
            "energy",
            "bowel",
        ]
        rendered = format_question_for_send(question, language=language)
        assert "1. " in rendered
        # 出口跟在每道选择题后面，不再逐题写进题干。
        assert rendered.splitlines()[-1] == (
            "（回编号就行，也可以直接说你的情况）"
            if language == "zh"
            else "(Just reply with the number, or tell me in your own words.)"
        )


def test_other_health_has_no_fake_other_option():
    """不设"其他"选项：客户说别的走"答了但对不上任何一项"加原话，不被一个假选项吃掉。"""
    from app.services.final_review_scripts import OPTION_KEYS

    assert "other" not in OPTION_KEYS[Q_OTHER_HEALTH]
    nodes = seed_nodes("zh")
    question = next(
        item for item in nodes[NODE_GOAL]["questions"] if item["id"] == Q_OTHER_HEALTH
    )
    question["options"].append({"key": "other", "text": "其他"})
    with pytest.raises(ValueError, match="选项标识"):
        normalize_nodes(nodes)


@pytest.mark.asyncio
async def test_a_version_that_no_longer_validates_falls_back_to_seed(db_session):
    """回归 2026-09-07 生产事故：收紧校验让库里的历史版本当场变成非法。

    那次把"想改善哪一项"从开放题改成选择题，旧版本随之缺了必需的选项，读话术
    直接抛错、整条复盘链路瘫掉。读取必须能顶住——最坏是发上一版措辞，不能瘫。
    """
    from app.models.final_review_script_version import FinalReviewScriptVersion

    nodes = seed_nodes("zh")
    for question in nodes[NODE_GOAL]["questions"]:
        if question["id"] == Q_OTHER_HEALTH:
            question["options"] = []  # 模拟"按今天的校验已不合规"的历史版本
    db_session.add(
        FinalReviewScriptVersion(language="zh", version_no=1, nodes_json=nodes)
    )
    await db_session.flush()

    script = await get_active_script(db_session, language="zh")

    assert script.version_no is None  # 回落到了种子
    assert script.question(Q_OTHER_HEALTH)["options"]  # 发出去的仍是合法话术


@pytest.mark.asyncio
async def test_node_sends_the_configured_script_verbatim(db_session, monkeypatch):
    """主动开口的四条不经过模型：发出去的必须与后台配置逐字一致。

    2026-09-07 实测里模型给权益那条加了句配置里没有的"权益不领也不会消失"
    （那张券只有 5 天有效期），还把话术原文和自己的开场说了两遍。
    """
    from app.core.config import get_settings
    from app.services import reminder_service as reminder_module
    from app.services.final_review_runner import plan_node

    sent: list[tuple[str, str]] = []

    async def fake_send(group_id, text, **kwargs):
        sent.append((group_id, text))
        return SimpleNamespace(success=True, message_id="m-1", error=None)

    monkeypatch.setattr(
        reminder_module, "_send_channel_text_with_retries", fake_send
    )

    case, _, _ = await _archivist_case(db_session, monkeypatch)
    case.service_plan_start_date = date.today() - timedelta(days=26)
    await db_session.flush()
    await db_session.commit()

    script = await get_active_script(db_session, language="zh")
    progress = await load_progress(db_session, service_case_id=case.id)
    expected = await plan_node(
        db_session,
        service_case=case,
        script=script,
        node=NODE_RESULTS,
        progress=progress,
    )
    await db_session.rollback()

    from app.services.reminder_service import ReminderService

    now = datetime.combine(
        date.today(), FINAL_REVIEW_WINDOW[1], tzinfo=timezone.utc
    ) - timedelta(minutes=1)
    await ReminderService(get_settings()).check_final_review_once(now=now)

    assert sent, "第26天的节点应该发出一条消息"
    assert sent[0][1] == expected.text
    # 模型没被调用过——发出去的就是话术原文。
    assert get_settings().final_review_enabled is True


def test_benefit_list_keeps_one_coupon_per_line():
    """券清单一张一行。压平成一行会让链接后面紧跟下一张券的名字，客户点错。"""
    benefits_text = describe_benefits(
        [
            Day28Benefit(**_LIVE_COUPON),
            Day28Benefit(
                code="member_fixed",
                name="定额会员券",
                face_value="40元",
                validity_days=30,
                claim_url="#微信小店://店铺/优惠券/abc",
                enabled=True,
            ),
        ]
    )
    rendered = render_question_text(
        "你这28天的权益已经可以领了：{权益清单}", benefits_text=benefits_text
    )
    lines = [line for line in rendered.splitlines() if line.strip()]
    # 题干一行，每张券两行（条件 + 链接），且第一张不许挤在冒号后面。
    assert lines == [
        "你这28天的权益已经可以领了：",
        "240元复购券（240元，5天内有效，适用于高燃人生早C午B晚A28日套装，满600-240）",
        "#微信小店://Cephei奢啡加速燃/优惠券/kVQsb47P9dnOkEA",
        "定额会员券（40元，30天内有效）",
        "#微信小店://店铺/优惠券/abc",
    ]
    assert "\n\n240元复购券" in rendered  # 清单前留一个空行
    assert "kVQsb47P9dnOkEA\n\n定额会员券" in rendered  # 两张券之间也隔一行


# --------------------------------------------------------------------------
# 两道题不再重叠 + 权益补发（2026-09-07 实测暴露的两个坑）
# --------------------------------------------------------------------------


def test_service_intent_no_longer_repeats_the_goal_question():
    """第29天那题不再问"想看别的产品"——第27天的目标题已经问过同一件事。

    实测里客户隔一天看到两组几乎一样的选项，会以为系统没记住他答过。
    """
    nodes = seed_nodes("zh")
    intent = next(
        item for item in nodes[NODE_BENEFIT]["questions"] if item["id"] == Q_SERVICE_INTENT
    )
    goal = next(
        item for item in nodes[NODE_GOAL]["questions"] if item["id"] == Q_NEXT_GOAL
    )
    assert "explore_other" not in {option["key"] for option in intent["options"]}
    assert "daily_product" in {option["key"] for option in goal["options"]}
    # 题干也要划清范围：一道问健康目标，一道问下一期的产品与服务安排。
    assert "下一期" in intent["text"]
    assert "最想优先做" in goal["text"]


def test_service_intent_english_seed_dropped_the_same_option():
    intent = next(
        item
        for item in seed_nodes("en")[NODE_BENEFIT]["questions"]
        if item["id"] == Q_SERVICE_INTENT
    )
    assert "explore_other" not in {option["key"] for option in intent["options"]}


@pytest.mark.asyncio
async def test_daily_product_goal_counts_as_continue(db_session, create_case):
    """第27天说"主要找日常喝的"、第29天选"暂时不购买"：按继续处理。

    只看第29天会把他归成暂缓，第30天就去问"是什么让你先缓一缓"——他要的其实是
    换个产品买，问反了。
    """
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_NEXT_GOAL,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["daily_product"]},
    )
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_SERVICE_INTENT,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["no_purchase_self"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert progress.intent_bucket == INTENT_CONTINUE

    script = await get_active_script(db_session, language="zh")
    asked = {
        question["id"]
        for question in pending_questions(script.questions_for(NODE_HANDOFF), progress)
    }
    assert Q_REASON_CONTINUE in asked
    assert Q_REASON_PAUSE not in asked


@pytest.mark.asyncio
async def test_pause_stays_pause_without_cross_product_intent(db_session, create_case):
    """没有跨品意向时"暂时不购买"照旧归暂缓——上一条不能把暂缓桶清空。"""
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_NEXT_GOAL,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["maintain"]},
    )
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_SERVICE_INTENT,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["no_purchase_self"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert progress.intent_bucket == INTENT_PAUSE


@pytest.mark.asyncio
async def test_cross_product_intent_alone_asks_no_reason_question(
    db_session, create_case
):
    """第29天那题还没答时，两套原因问法都不问——不拿第27天的答案替他表态。"""
    case_id = await _new_case(create_case)
    await record_answer(
        db_session,
        service_case_id=case_id,
        question_id=Q_NEXT_GOAL,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["daily_product"]},
    )
    progress = await load_progress(db_session, service_case_id=case_id)
    assert progress.cross_product_intent is True
    assert progress.intent_bucket is None


def test_topup_wording_is_never_offered_as_a_pending_question():
    """补发那句话术不是提问：按天推进的节点和助教的进度工具都不许把它当题问。"""
    from app.services.final_review_scripts import FINAL_REVIEW_QUESTION_IDS

    assert Q_BENEFIT_TOPUP not in FINAL_REVIEW_QUESTION_IDS
    script = FinalReviewScript(
        language="zh", version_no=None, nodes=seed_nodes("zh")
    )
    progress = FinalReviewProgress(answers={})
    asked = {
        question["id"]
        for question in pending_questions(script.questions_for(NODE_BENEFIT), progress)
    }
    assert Q_BENEFIT_TOPUP not in asked


_TOPUP_CONFIG = {
    "benefits": [
        {
            "code": "product_200",
            "name": "240元复购券",
            "face_value": "240元",
            "validity_days": 5,
            "claim_url": "#微信小店://店铺/优惠券/always",
            "enabled": True,
            "grant_when": GRANT_ALWAYS,
        },
        {
            "code": "member_fixed",
            "name": "定额会员券",
            "face_value": "40元",
            "validity_days": 30,
            "claim_url": "#微信小店://店铺/优惠券/member",
            "enabled": True,
            "grant_when": GRANT_CROSS_PRODUCT_INTENT,
        },
    ]
}


async def _case_after_benefit_node(db_session, monkeypatch, *, cross_intent: bool):
    """权益消息已发出、只发了无条件券的服务单。"""
    case, _, _ = await _archivist_case(db_session, monkeypatch)
    await set_benefit_config(db_session, _TOPUP_CONFIG)
    await reserve_grant(
        db_session, service_case_id=case.id, benefit_code="product_200"
    )
    if cross_intent:
        # 权益消息发出后才出现的跨品意向（客户后来改口说想看别的）。
        await record_answer(
            db_session,
            service_case_id=case.id,
            question_id=Q_NEXT_GOAL,
            answer_status=STATUS_ANSWERED,
            normalized_value={"selected": ["daily_product"]},
        )
    await db_session.flush()
    return case


@pytest.mark.asyncio
async def test_benefit_topup_sends_the_coupon_that_the_node_could_not(
    db_session, monkeypatch
):
    """权益消息发出后才说想看别的产品：那张会员券要补发，不能烂在账上。"""
    from app.services.final_review_runner import plan_benefit_topup

    case = await _case_after_benefit_node(db_session, monkeypatch, cross_intent=True)
    script = await get_active_script(db_session, language="zh")
    progress = await load_progress(db_session, service_case_id=case.id)
    plan = await plan_benefit_topup(
        db_session,
        service_case=case,
        script=script,
        progress=progress,
        asked_nodes=[NODE_BENEFIT],
    )
    assert plan is not None
    assert [benefit.code for benefit in plan.reserved_benefits] == ["member_fixed"]
    assert "#微信小店://店铺/优惠券/member" in plan.text
    # 无条件券已经发过，不许在补发里再出现一次。
    assert "#微信小店://店铺/优惠券/always" not in plan.text
    # 补发用自己的话术，不重复"你这28天的权益已经可以领了"。
    assert "28天的权益" not in plan.text
    assert await granted_codes(db_session, service_case_id=case.id) == {
        "product_200",
        "member_fixed",
    }


@pytest.mark.asyncio
async def test_benefit_topup_does_nothing_without_cross_product_intent(
    db_session, monkeypatch
):
    from app.services.final_review_runner import plan_benefit_topup

    case = await _case_after_benefit_node(db_session, monkeypatch, cross_intent=False)
    script = await get_active_script(db_session, language="zh")
    progress = await load_progress(db_session, service_case_id=case.id)
    assert (
        await plan_benefit_topup(
            db_session,
            service_case=case,
            script=script,
            progress=progress,
            asked_nodes=[NODE_BENEFIT],
        )
        is None
    )


@pytest.mark.asyncio
async def test_benefit_topup_waits_for_the_benefit_message(db_session, monkeypatch):
    """权益消息还没发就不补——那张券本来就会跟着权益消息一起发出去。"""
    from app.services.final_review_runner import plan_benefit_topup

    case = await _case_after_benefit_node(db_session, monkeypatch, cross_intent=True)
    script = await get_active_script(db_session, language="zh")
    progress = await load_progress(db_session, service_case_id=case.id)
    assert (
        await plan_benefit_topup(
            db_session,
            service_case=case,
            script=script,
            progress=progress,
            asked_nodes=[NODE_RESULTS, NODE_GOAL],
        )
        is None
    )


@pytest.mark.asyncio
async def test_benefit_topup_never_sends_the_same_coupon_twice(
    db_session, monkeypatch
):
    """台账唯一约束是防线：补过一次就再也不补，重复发券是直接的钱损失。"""
    from app.services.final_review_runner import plan_benefit_topup

    case = await _case_after_benefit_node(db_session, monkeypatch, cross_intent=True)
    script = await get_active_script(db_session, language="zh")
    progress = await load_progress(db_session, service_case_id=case.id)
    first = await plan_benefit_topup(
        db_session,
        service_case=case,
        script=script,
        progress=progress,
        asked_nodes=[NODE_BENEFIT],
    )
    assert first is not None
    assert (
        await plan_benefit_topup(
            db_session,
            service_case=case,
            script=script,
            progress=progress,
            asked_nodes=[NODE_BENEFIT],
        )
        is None
    )


@pytest.mark.asyncio
async def test_benefit_topup_falls_back_to_seed_wording(db_session, monkeypatch):
    """库里的旧版本没有补发那句话术时用种子发出去。

    宁可用种子那句话把领取入口发出去，也不能因为缺文案让券停在"有资格、没发出"
    ——那正是这次要修的 bug：不报错、不入账、客户什么也没收到。
    """
    from app.services.final_review_runner import plan_benefit_topup

    case = await _case_after_benefit_node(db_session, monkeypatch, cross_intent=True)
    legacy = seed_nodes("zh")
    legacy[NODE_BENEFIT]["questions"] = [
        item
        for item in legacy[NODE_BENEFIT]["questions"]
        if item["id"] != Q_BENEFIT_TOPUP
    ]
    script = await save_script_version(db_session, language="zh", nodes=legacy)
    assert script.question(Q_BENEFIT_TOPUP) is None
    progress = await load_progress(db_session, service_case_id=case.id)
    plan = await plan_benefit_topup(
        db_session,
        service_case=case,
        script=script,
        progress=progress,
        asked_nodes=[NODE_BENEFIT],
    )
    assert plan is not None
    assert "#微信小店://店铺/优惠券/member" in plan.text


@pytest.mark.asyncio
async def test_scheduler_tops_up_the_coupon_after_the_round_is_over(
    db_session, monkeypatch
):
    """整轮节点都推完之后，调度器仍会在次日窗口把漏掉的那张券补发出去。"""
    from app.core.config import get_settings
    from app.models.reminder import ReminderEvent, ReminderEventType
    from app.services import reminder_service as reminder_module
    from app.services.final_review_runner import (
        BENEFIT_TOPUP_RULE_ID,
        FINAL_REVIEW_NODES,
        FINAL_REVIEW_WINDOW,
    )
    from app.services.reminder_service import ReminderService

    sent: list[tuple[str, str]] = []

    async def fake_send(group_id, text, **kwargs):
        sent.append((group_id, text))
        return SimpleNamespace(success=True, message_id="m-topup", error=None)

    monkeypatch.setattr(reminder_module, "_send_channel_text_with_retries", fake_send)

    case = await _case_after_benefit_node(db_session, monkeypatch, cross_intent=True)
    case.service_plan_start_date = date.today() - timedelta(days=31)
    for node in FINAL_REVIEW_NODES:
        db_session.add(
            ReminderEvent(
                service_case_id=case.id,
                rule_id=f"final_review:{node}",
                event_date=date.today() - timedelta(days=1),
                event_type=ReminderEventType.REMINDED,
            )
        )
    await db_session.flush()
    await db_session.commit()

    now = datetime.combine(
        date.today(), FINAL_REVIEW_WINDOW[1], tzinfo=timezone.utc
    ) - timedelta(minutes=1)
    await ReminderService(get_settings()).check_final_review_once(now=now)

    assert len(sent) == 1
    assert "#微信小店://店铺/优惠券/member" in sent[0][1]
    assert await granted_codes(db_session, service_case_id=case.id) == {
        "product_200",
        "member_fixed",
    }
    gate = await db_session.scalar(
        select(ReminderEvent.id).where(
            ReminderEvent.service_case_id == case.id,
            ReminderEvent.rule_id == BENEFIT_TOPUP_RULE_ID,
        )
    )
    assert gate is not None

    # 同一天再跑一次不会重发。
    sent.clear()
    await ReminderService(get_settings()).check_final_review_once(now=now)
    assert sent == []


@pytest.mark.asyncio
async def test_the_goal_question_stays_reportable_after_it_was_answered(
    db_session, monkeypatch
):
    """目标题答过了仍留在待答清单里——客户改口说想找日常喝的，那张券才补得出去。

    别的题答过就移出清单，避免模型把闲聊里顺嘴一句当成正式表态。
    """
    from app.models.reminder import ReminderEvent, ReminderEventType
    from app.services.archivist.graph import ArchivistPipeline

    case, _, progress_row = await _archivist_case(db_session, monkeypatch)
    db_session.add(
        ReminderEvent(
            service_case_id=case.id,
            rule_id="final_review:goal",
            event_date=date.today() - timedelta(days=2),
            event_type=ReminderEventType.REMINDED,
        )
    )
    await record_answer(
        db_session,
        service_case_id=case.id,
        question_id=Q_NEXT_GOAL,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["maintain"]},
    )
    await record_answer(
        db_session,
        service_case_id=case.id,
        question_id=Q_SERVICE_INTENT,
        answer_status=STATUS_ANSWERED,
        normalized_value={"selected": ["continue_original"]},
    )
    await db_session.flush()

    pipeline = ArchivistPipeline(
        db_session, service_case=case, progress=progress_row
    )
    pending = await pipeline._final_review_context()
    by_id = {item["question_id"]: item for item in pending}
    assert by_id[Q_NEXT_GOAL]["already_answered"] is True
    assert Q_SERVICE_INTENT not in by_id
    assert Q_BENEFIT_TOPUP not in by_id
