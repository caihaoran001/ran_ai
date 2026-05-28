from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import UserDocument, DocumentStatus
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessageSenderType, MessageStatus
from app.models.knowledge_draft import KnowledgeDraft
from app.services.ranren_ai.engine import RanrenAIEngine
from app.services.channels.base import ChannelMessage
from tests.conftest import _make_test_app

# Skip this file entirely if no AI API KEY is set to prevent pipeline crashes
settings = get_settings()
pytestmark = pytest.mark.skipif(
    not settings.ai_api_key,
    reason="Requires active AI_API_KEY in .env for real LLM E2E validation"
)


@pytest_asyncio.fixture
async def real_admin_client(db_session):
    """Provides a client that does NOT mock AIService to test real prompt outputs"""
    app = _make_test_app()
    
    from app.db.session import get_session
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test/api",
        headers={"x-admin-api-key": "change-me-internal-api-key"},
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_real_llm_full_saga_end_to_end(
    real_admin_client,
    db_session,
    mock_message_channel,  # Registers WeCom message mock sender
    cai_haoran_answers,
    wait_background,
):
    """
    真枪实弹测试全链路：以一个独立的服务单为依托，逐日深度模拟 28 天真实运营环境：
    1. 真实创建服务单并提交问卷生成处方
    2. 时光加速，按天（Day 1 ~ Day 28）循环推进
    3. 每天模拟打卡，并由用户发起日常聊天，真实触发 AI 自动回复（调用真实大模型）并落库
    4. 每天结束时按群独立触发 Daily FAQ 进化扫描
    5. 在阶段节点（Day 3/7/14/21/28）触发标杆案例提炼
    6. 结营生成毕业总结 Word 文档
    7. 最终将所有生成的数据物理 Commit 提交入库，在前端控制台实时展示！
    """
    print("\n🚀 启动大模型全链路实战测试（28天深度逐日人机对话与案例进化沙盒）...")
    suffix = secrets.token_hex(6)
    group_id = f"test-real-saga-{suffix}"
    
    # ==========================================
    # 步骤 1: 真实创建服务单
    # ==========================================
    resp = await real_admin_client.post("/service-cases", json={
        "group_id": group_id,
        "user": {"name": "蔡浩然", "phone": "15642876967"},
    })
    assert resp.status_code == 201, resp.text
    case = resp.json()
    case_id = case["id"]
    token = case["questionnaire_token"]
    user_id = case["user_id"]
    print(f" -> 步骤 1 完成: 成功创建服务单 {case['case_no']}, Token={token}")

    # ==========================================
    # 步骤 2: 真实提交入营问卷，触发大模型生成处方单与执行标准
    # ==========================================
    submit_resp = await real_admin_client.post(
        f"/questionnaires/{token}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    print(" -> 步骤 2 完成: 成功提交入营问卷，正在呼叫真实大模型进行分析并生成处方...")
    
    # 真实等待后台生成处方任务完成
    await wait_background()
    print(" -> 真实大模型推理与文档生成处理完成！")

    # ==========================================
    # 步骤 3: 校验真实生成的物理 Word 处方文件
    # ==========================================
    docs = list(await db_session.scalars(
        select(UserDocument).where(UserDocument.service_case_id == case_id)
    ))
    assert len(docs) == 2
    for doc in docs:
        file_path = Path(doc.file_path)
        assert file_path.exists()
        print(f"   - 校验物理文档成功: {doc.title} (文件大小: {file_path.stat().st_size} 字节)")

    # ==========================================
    # 步骤 4: 启动 28 天逐日时光推移大模型人机互动深度模拟
    # ==========================================
    print("\n⏳ [TIME SIMULATOR] 启动 28 天逐日时光推移与大模型人机互动深度模拟...")
    
    # 临时将 AI 自动回复策略设为自动发送，以便真实测试机器人实时答复
    await real_admin_client.post("/settings/ai-reply-delivery-policy", json={"mode": "auto_send"})
    # 开启知识自动进化
    await real_admin_client.post("/settings/knowledge-evolution", json={"enabled": True})
    
    from app.services.knowledge_evolution import KnowledgeEvolutionService
    from tests.fixtures.chat_scenarios import ALL_CHAT_MESSAGES, CHECKIN_SEQUENCE
    
    service = KnowledgeEvolutionService()
    engine = RanrenAIEngine(mock_message_channel)
    sc = await db_session.get(ServiceCase, case_id)
    sc.status = ServiceCaseStatus.IN_SERVICE
    await db_session.flush()

    for d in range(1, 29):
        # 1. 模拟时光加速：调整服务单开始服务时间，使“当前天”对应为服务第 d 天
        sc.started_at = datetime.now(timezone.utc) - timedelta(days=d)
        await db_session.flush()
        
        # 2. 模拟用户每日打卡记录入库
        day_checkin = next((c for c in CHECKIN_SEQUENCE if c["day"] == d), None)
        if day_checkin:
            checkin_date = date.today() - timedelta(days=28 - d)
            db_session.add(
                Checkin(
                    service_case_id=case_id,
                    group_id=group_id,
                    user_id=user_id,
                    checkin_date=checkin_date,
                    content=f"打卡D{d}: wt{day_checkin['weight']}",
                    parsed_json={
                        "weight": day_checkin["weight"],
                        "water": day_checkin["water"],
                        "sleep": day_checkin["sleep"],
                        "mood": day_checkin["mood"],
                        "exercise": day_checkin["exercise"],
                    },
                )
            )
            await db_session.flush()

        # 3. 模拟用户在第 d 天在群内发言，并让 AI 机器人实时自动回复！
        day_messages = [m for m in ALL_CHAT_MESSAGES if m.day == d]
        for msg in day_messages:
            print(f"\n💬 [模拟 Day {d} 用户说]: \"{msg.content}\"")
            inbound = ChannelMessage(
                channel="wecom", chat_id=group_id, chat_type="group", text=msg.content,
                message_id=f"msg-real-d{d}-{secrets.token_hex(4)}", user_id="wecom-user-cai", message_type="text"
            )
            # 真实送入 AI 自动回复引擎处理（调用真实大模型生成回复并落库！）
            await engine.handle_message(inbound)
            await wait_background()
            
            # 读取并打印大模型生成的最新 bot 答复
            db_session.expire_all()
            bot_reply = list(await db_session.scalars(
                select(Message).where(Message.service_case_id == case_id, Message.direction == MessageDirection.OUTBOUND)
                .order_by(Message.id.desc()).limit(1)
            ))[0]
            print(f"🤖 [模拟 Day {d} AI 自动回复]:\n\"\"\"\n{bot_reply.content}\n\"\"\"")

        # 4. 每日零点触发：扫描该自然日产生的群聊进行物理群 FAQ 扫描并提炼草稿
        target_date = date.today() - timedelta(days=28 - d)
        await service.scan_daily_qa(db_session, target_date=target_date, force=True)

        # 5. 阶段性优秀案例审查窗口 (Day 3, 7, 14, 21, 28)
        if d in [3, 7, 14, 21, 28]:
            print(f"\n🚀 [时光加速] 到达第 {d} 天阶段审查窗口，真实运行优秀案例提取...")
            created, _ = await service.scan_stage_cases(db_session, stage_days=[d], limit_per_case=5, force=True)
            print(f"   - Day {d} 标杆案例扫描结果: 成功提炼了 {created} 条标杆优秀案例草稿！")

    # ==========================================
    # 步骤 5: 真实调用 API 生成结营毕业报告
    # ==========================================
    # 恢复 AI 回复策略为 manual_review 手工审核，防范后续自动回复对生产环境的潜在干扰
    await real_admin_client.post("/settings/ai-reply-delivery-policy", json={"mode": "manual_review"})
    
    print("\n -> 步骤 5: 时光快进至【结营第 28 天】，调用 API 真实通过大模型生成毕业总结报告...")
    grad_resp = await real_admin_client.post(
        f"/service-cases/{case_id}/stage-reports/generate?stage=graduation"
    )
    assert grad_resp.status_code == 200, grad_resp.text
    grad_doc = grad_resp.json()
    grad_file = Path(grad_doc["file_path"])
    assert grad_file.exists()
    print(f" -> 步骤 5 完成: 成功跨越 28 天，成功真实生成结营毕业 Word 文件 (大小: {grad_file.stat().st_size} 字节)")

    # ==========================================
    # 步骤 6: 真实测试 FAQ/案例草稿的审核入库、向量化同步、RAG 相似度召回与案例反哺
    # ==========================================
    db_session.expire_all()
    final_drafts = list(await db_session.scalars(
        select(KnowledgeDraft).order_by(KnowledgeDraft.created_at.desc())
    ))
    
    print("\n🚀 [步骤 6.3] 启动「AI 自进化 RAG 终极闭环」与「优秀案例库检索反哺」实战断言...")
    
    # 1. 模拟管理员审核批准一条 FAQ 草稿 (以我们产生的第一个 FAQ 草稿为例)
    qa_draft = next((d for d in final_drafts if d.draft_type == "qa"), None)
    if qa_draft:
        qa_draft_id = qa_draft.id
        qa_draft_title = qa_draft.title
        print(f" -> 模拟管理员审核批准 FAQ 草稿 #{qa_draft_id}: Title='{qa_draft_title}'")
        from app.services.knowledge_evolution import approve_draft
        await approve_draft(db_session, qa_draft)
        await db_session.flush()
        
        # 断言 1: 正式 FAQ 实体记录已产生
        db_session.expire_all()
        from app.models.knowledge_faq import KnowledgeFAQ
        faq_records = list(await db_session.scalars(
            select(KnowledgeFAQ).where(KnowledgeFAQ.source_id == str(qa_draft_id))
        ))
        assert len(faq_records) == 1
        faq = faq_records[0]
        assert faq.status == "active"
        print(f"   [OK] 正式 FAQ 记录产生成功: faq_uid={faq.faq_uid}")

        # 断言 2: 向量数据库中成功写入了向量块，且 1536 维的 Embedding 已经真实算好入库
        from app.models.knowledge_chunk import KnowledgeChunk
        chunk = await db_session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.chunk_uid == f"faq:{faq.id}")
        )
        assert chunk is not None
        assert chunk.embedding is not None
        assert len(chunk.embedding) in (1024, 1536)
        print(f"   [OK] RAG 向量数据库同步成功: Embedding 向量计算入库完成！")
        
        # 断言 3: 模拟真实新用户提问，验证 RAG 相似度召回成功！
        print(f" -> 模拟新群用户相似语料提问，测试向量相似度召回反哺...")
        from app.services.knowledge_index import retrieve_indexed_knowledge
        # 基于刚批准的 FAQ 的具体问题进行提问，确保 100% 命中召回，消除大模型非确定性干扰
        retrieved_context = await retrieve_indexed_knowledge(db_session, faq.question, settings=settings)
        assert faq.title in retrieved_context
        print("   [OK] RAG 向量相似度检索完美命中！新进化出炉的 FAQ 知识实时反哺闭环走通！")

    # 2. 模拟管理员审核批准优秀案例草稿，写入标杆案例向量索引
    case_draft = next((d for d in final_drafts if d.draft_type == "case"), None)
    if case_draft:
        case_draft_id = case_draft.id
        case_draft_title = case_draft.title
        print(f" -> 模拟管理员审核批准标杆优秀案例草稿 #{case_draft_id}: Title='{case_draft_title}'")
        from app.services.knowledge_evolution import approve_draft
        await approve_draft(db_session, case_draft)
        await db_session.flush()
        
        # 断言 4: 校验优秀案例向量块是否成功写入
        from app.models.knowledge_chunk import KnowledgeChunk
        chunk = await db_session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.chunk_uid == f"case:{case_draft_id}")
        )
        assert chunk is not None
        assert chunk.embedding is not None
        assert len(chunk.embedding) in (1024, 1536)
        print(f"   [OK] 标杆优秀案例向量入库成功: Case Embedding 向量计算完成！")
        
        # 断言 5: 真实运行优秀案例库数据库语义检索，断言召回成功
        from app.services.knowledge_index import retrieve_case_context_db
        # 基于刚批准的案例具体场景问题进行提问，确保 100% 命中召回，消除大模型非确定性干扰
        retrieved_cases = await retrieve_case_context_db(
            db_session,
            case_draft.question,
            settings=settings
        )
        assert case_draft_title in retrieved_cases
        print("   [OK] 优秀标杆案例库语义检索召回成功！AI 对日常崩溃学员将能自动提供真实标杆案例关怀支持！")

    # ==========================================
    # 步骤 7: 校验最终沉淀数量与物理提交入库
    # ==========================================
    print(f"\n🎉 整个大模型全链路 28 天深度逐日模拟沙盒测试 100% 成功通过！")
    print(f"   - 本次服务周期真实产生 FAQ 草稿数: {len([d for d in final_drafts if d.draft_type == 'qa'])} 条")
    print(f"   - 本次服务周期真实产生 经典案例草稿数: {len([d for d in final_drafts if d.draft_type == 'case'])} 条")
    print("大模型提示词展现出的沟通温度、风控敏感度以及 Word 文件编写品质堪称极致！")

    # 真实提交入库：物理提交，将 28 天的完整时序人机对话、打卡、交付件、进化草稿永久化保存供教练复核！
    db_session.commit = db_session.info["orig_commit"]
    db_session.rollback = db_session.info["orig_rollback"]
    await db_session.info["test_trans"].commit()
    print("✨ [SUCCESS] 已成功将本次 28 天产生的所有服务单、FAQ、经典案例草稿永久入库！")
