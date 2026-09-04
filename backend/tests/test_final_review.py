"""第28天结营复盘：话术配置、客户答案、券配置与发放台账。

这一层刻意全是确定性逻辑，不涉及模型调用——话术从库里取、该问哪题按已有答案算、
券发不发按配置和台账算。覆盖重点是几处"错了不会报错、只会静默失效"的地方：

- 话术改库不改码就要生效（本仓库在入营问卷模板上踩过：改了代码默认值，线上没变化）。
- 选项语义 key 必须稳定：运营改文案不能让"发会员券""少打扰生效"这些分支失灵。
- 客户跳过、拒答、答不清要各自成立，不能混成"没答"。
- 同一张券对同一服务单只能发一次（重复发券是直接的钱损失）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

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
from app.services.final_review_scripts import (
    ASK_INTENT_CONTINUE,
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
        if option["key"] == "explore_other":
            option["text"] = "想尝尝你们别的咖啡"

    normalized = normalize_nodes(nodes)
    rewritten = next(
        question
        for question in normalized[NODE_BENEFIT]["questions"]
        if question["id"] == Q_SERVICE_INTENT
    )
    texts = {option["key"]: option["text"] for option in rewritten["options"]}
    assert texts["explore_other"] == "想尝尝你们别的咖啡"
    # key 没变，所以"发跨品会员券"那条分支照旧成立。
    assert "explore_other" in texts


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


def test_send_format_lists_options_without_numbering():
    """选项不编号：客户回"A"还是回原话都得能对上，编号会和运营改过的顺序错位。"""
    question = {
        "id": Q_NEXT_GOAL,
        "text": "接下来你最想优先做什么？",
        "options": [
            {"key": "lose_weight", "text": "继续减重／减脂"},
            {"key": "maintain", "text": "维持现在的状态"},
        ],
    }
    rendered = format_question_for_send(question)
    assert rendered.splitlines() == [
        "接下来你最想优先做什么？",
        "继续减重／减脂",
        "维持现在的状态",
    ]
    assert "A." not in rendered
    assert "1." not in rendered


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
    assert describe_benefit(bare) == "200元产品券：https://example.com/a"

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
    assert "http" in response.json()["detail"]


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
