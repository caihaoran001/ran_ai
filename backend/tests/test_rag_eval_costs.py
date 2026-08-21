from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.core.config import get_settings
from app.evaluation.rag_cases import load_rag_eval_suite
from eval.rag_costs import (
    RagEvaluationCostLedger,
    install_rag_usage_collectors,
    instrument_openai_client,
    summarize_case_usage,
)
from eval.run_full_eval import _mock_cases


def _settings(**updates):
    return get_settings().model_copy(
        update={
            "ai_prompt_cost_per_1m_tokens": 2.0,
            "ai_completion_cost_per_1m_tokens": 4.0,
            "ai_prompt_cache_hit_cost_per_1m_tokens": 1.0,
            "ai_peak_price_multiplier": 1.0,
            "embedding_cost_per_1m_tokens": 0.5,
            **updates,
        }
    )


@pytest.mark.asyncio
async def test_rag_cost_ledger_tracks_four_components_and_actual_models():
    ledger = RagEvaluationCostLedger(_settings())
    ledger.begin_case("rag-cost-case")

    with ledger.stage("intent_rewrite"):
        await ledger.record_intent_model_usage(
            model="configured-intent",
            usage=SimpleNamespace(
                prompt_tokens=1_000,
                completion_tokens=500,
                total_tokens=1_500,
                prompt_cache_hit_tokens=200,
            ),
            elapsed_ms=120,
            attempts=1,
            success=True,
            scenario="message_insight",
        )
    with ledger.stage("answer_generation"):
        await ledger.record_answer_model_usage(
            model="configured-answer",
            response=SimpleNamespace(
                usage_metadata={
                    "input_tokens": 2_000,
                    "output_tokens": 1_000,
                    "total_tokens": 3_000,
                },
                response_metadata={"model_name": "actual-answer"},
            ),
            elapsed_ms=200,
            success=True,
            scenario="reply_agent",
        )
    with ledger.stage("faithfulness"):
        ledger.record_openai_response(
            component="ragas_judge",
            configured_model="configured-judge",
            response=SimpleNamespace(
                model="actual-judge",
                usage=SimpleNamespace(
                    prompt_tokens=3_000,
                    completion_tokens=1_000,
                    total_tokens=4_000,
                ),
            ),
            elapsed_ms=300,
            success=True,
            error_type=None,
        )
    with ledger.stage("answer_relevancy"):
        ledger.record_openai_response(
            component="embedding",
            configured_model="configured-embedding",
            response=SimpleNamespace(
                model="actual-embedding",
                usage=SimpleNamespace(prompt_tokens=10_000, total_tokens=10_000),
            ),
            elapsed_ms=80,
            success=True,
            error_type=None,
        )

    usage = ledger.case_usage("rag-cost-case")
    ledger.end_case("rag-cost-case")

    assert usage.tracking_available is True
    assert usage.total.call_count == 4
    assert usage.total.request_count == 4
    assert usage.total.complete is True
    assert usage.total.estimated_cost_yuan == pytest.approx(0.0268)
    assert usage.intent.prompt_cache_hit_tokens == 200
    assert usage.answer.estimated_cost_yuan == pytest.approx(0.008)
    assert usage.ragas_judge.estimated_cost_yuan == pytest.approx(0.01)
    assert usage.embedding.estimated_cost_yuan == pytest.approx(0.005)
    assert [item.model for item in usage.calls] == [
        "configured-intent",
        "actual-answer",
        "actual-judge",
        "actual-embedding",
    ]
    assert [item.stage_key for item in usage.calls] == [
        "intent_rewrite",
        "answer_generation",
        "faithfulness",
        "answer_relevancy",
    ]


@pytest.mark.asyncio
async def test_rag_cost_is_incomplete_when_price_usage_or_retry_detail_is_missing():
    ledger = RagEvaluationCostLedger(
        _settings(
            ai_prompt_cost_per_1m_tokens=0.0,
            ai_completion_cost_per_1m_tokens=0.0,
            ai_prompt_cache_hit_cost_per_1m_tokens=0.0,
            embedding_cost_per_1m_tokens=0.0,
        )
    )
    ledger.begin_case("rag-incomplete")
    await ledger.record_intent_model_usage(
        model="intent-model",
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        ),
        elapsed_ms=1,
        attempts=2,
        success=True,
        scenario="message_insight",
    )
    ledger.record_openai_response(
        component="embedding",
        configured_model="embedding-model",
        response=SimpleNamespace(usage=None),
        elapsed_ms=1,
        success=False,
        error_type="TimeoutError",
    )

    usage = ledger.case_usage("rag-incomplete")
    ledger.end_case("rag-incomplete")

    assert usage.total.call_count == 2
    assert usage.total.request_count == 3
    assert usage.total.known_estimated_cost_yuan == 0
    assert usage.total.estimated_cost_yuan is None
    assert usage.total.complete is False
    assert usage.total.usage_reported_calls == 1


@pytest.mark.asyncio
async def test_openai_client_instrumentation_records_each_raw_request():
    response = SimpleNamespace(
        model="judge-actual",
        usage=SimpleNamespace(
            prompt_tokens=50,
            completion_tokens=10,
            total_tokens=60,
        ),
    )

    class Completions:
        async def create(self, **kwargs):
            assert kwargs["model"] == "judge-configured"
            return response

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()),
        embeddings=SimpleNamespace(),
    )
    ledger = RagEvaluationCostLedger(_settings())
    ledger.begin_case("rag-wrapped")
    instrument_openai_client(client, ledger, chat_component="ragas_judge")

    with ledger.stage("context_recall"):
        returned = await client.chat.completions.create(model="judge-configured")

    usage = ledger.case_usage("rag-wrapped")
    ledger.end_case("rag-wrapped")
    assert returned is response
    assert usage.ragas_judge.call_count == 1
    assert usage.calls[0].model == "judge-actual"
    assert usage.calls[0].stage_key == "context_recall"


def test_mock_rag_cases_have_tracked_zero_cost_usage():
    cases = _mock_cases(load_rag_eval_suite().cases[:2])
    usage = summarize_case_usage(cases, tracking_available=True)

    assert all(case.usage.tracking_available for case in cases)
    assert usage.tracking_available is True
    assert usage.total.call_count == 0
    assert usage.total.estimated_cost_yuan == 0
    assert usage.total.complete is True


def test_rag_usage_collectors_restore_process_hooks():
    import app.services.agent.graph as graph_module
    import app.services.embedding_service as embedding_module
    from app.services.ai_service.client import AIService

    original_intent = AIService._record_usage
    original_answer = graph_module.record_model_usage
    original_openai = embedding_module.AsyncOpenAI
    restore = install_rag_usage_collectors(RagEvaluationCostLedger(_settings()))

    assert AIService._record_usage is not original_intent
    assert graph_module.record_model_usage is not original_answer
    assert embedding_module.AsyncOpenAI is not original_openai

    restore()

    assert AIService._record_usage is original_intent
    assert graph_module.record_model_usage is original_answer
    assert embedding_module.AsyncOpenAI is original_openai
