from __future__ import annotations

import asyncio
import os
import time
import json
import re
import secrets
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.services.ai_service import AIService
from app.models.ai_usage import AIUsageLog
from app.models.archivist_progress import ArchivistProgress
from app.models.archivist_quarantine import ArchivistQuarantine
from app.models.service_case import ServiceCase

router = APIRouter()
_DIAGNOSTICS_RUN_LOCK = asyncio.Lock()
HEALTH_ACTIVE_RUN_TIMEOUT_SECONDS = 30 * 60
REAL_E2E_ACTIVE_RUN_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_CONSOLE_LOG_LINES = 2000

TEST_SUITES = [
    {
        "id": "01",
        "display_id": "01",
        "name": "test_01_intake",
        "description": "入营问卷基本录入与数据合法性校验",
        "file": "tests/test_01_intake.py",
        "tier": "health",
        "included_in_health_check": True,
        "data_policy": "rollback",
        "requires_confirmation": False,
    },
    {
        "id": "02",
        "display_id": "02",
        "name": "test_02_chat",
        "description": "社群模拟群聊多轮对话与情绪/健康风控拦截测试",
        "file": "tests/test_02_chat.py",
        "tier": "health",
        "included_in_health_check": True,
        "data_policy": "rollback",
        "requires_confirmation": False,
    },
    {
        "id": "03",
        "display_id": "03",
        "name": "test_03_checkin",
        "description": "打卡数据创建、解析结构和排序校验测试",
        "file": "tests/test_03_checkin.py",
        "tier": "health",
        "included_in_health_check": True,
        "data_policy": "rollback",
        "requires_confirmation": False,
    },
    {
        "id": "04",
        "display_id": "04",
        "name": "test_04_stage_reports",
        "description": "3/7/14/21/28天阶段报告自动生成测试",
        "file": "tests/test_04_stage_reports.py",
        "tier": "health",
        "included_in_health_check": True,
        "data_policy": "rollback",
        "requires_confirmation": False,
    },
    {
        "id": "05",
        "display_id": "05",
        "name": "test_05_evolution",
        "description": "24小时群聊沉淀 FAQ 知识进化与打卡校验测试",
        "file": "tests/test_05_evolution.py",
        "tier": "health",
        "included_in_health_check": True,
        "data_policy": "rollback",
        "requires_confirmation": False,
    },
    {
        "id": "06",
        "display_id": "06",
        "name": "test_06_toggles",
        "description": "策略开关、知识进化配置与软删除校验测试",
        "file": "tests/test_06_toggles.py",
        "tier": "health",
        "included_in_health_check": True,
        "data_policy": "rollback",
        "requires_confirmation": False,
    },
    {
        "id": "07",
        "display_id": "07",
        "name": "test_07_qiwe_channel",
        "description": "QiWe 通道代码逻辑测试：消息聚合、去重、异常处理（使用 mock 对象，不测试真实通道）",
        "file": "tests/test_07_qiwe_channel.py",
        "tier": "health",
        "included_in_health_check": True,
        "data_policy": "rollback",
        "requires_confirmation": False,
    },
    {
        "id": "08",
        "display_id": "08",
        "name": "test_08_outcome_engine",
        "description": "成效引擎测试：LLM 提取 + 阶段状态更新 + 行动焦点评分 + 时间感知",
        "file": "tests/test_08_outcome_engine.py",
        "tier": "health",
        "included_in_health_check": True,
        "data_policy": "rollback",
        "requires_confirmation": False,
    },
]

HEALTH_TEST_SUITES = [suite for suite in TEST_SUITES if suite["included_in_health_check"]]

@router.get("/test-suites")
async def get_test_suites():
    """获取所有系统自测套件的元数据"""
    return TEST_SUITES


@router.get("/archivist-failures")
async def get_archivist_failures(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(50, ge=1, le=500),
):
    """运营可见的档案官失败入口：列出当前有连续失败（水位线卡住、未推进）的服务单。

    档案官一轮归档若 LLM 抽取或成效/画像写库抛错，不推进水位线、下轮自动重试，
    并在 archivist_progress 记连续失败计数与最后失败原因。这里把这些卡住的单暴露出来，
    便于运营发现并排查（只读，不触发任何写操作）。
    """
    rows = (
        await session.execute(
            select(ArchivistProgress, ServiceCase.case_no)
            .outerjoin(ServiceCase, ServiceCase.id == ArchivistProgress.service_case_id)
            .where(ArchivistProgress.consecutive_failures > 0)
            .order_by(
                ArchivistProgress.consecutive_failures.desc(),
                ArchivistProgress.last_failure_at.desc(),
            )
            .limit(limit)
        )
    ).all()
    total_failing = await session.scalar(
        select(func.count(ArchivistProgress.id)).where(
            ArchivistProgress.consecutive_failures > 0
        )
    )
    items = [
        {
            "service_case_id": progress.service_case_id,
            "case_no": case_no,
            "consecutive_failures": progress.consecutive_failures,
            "failure_count": progress.failure_count,
            "last_failure_at": progress.last_failure_at.isoformat()
            if progress.last_failure_at
            else None,
            "last_failure_reason": progress.last_failure_reason,
            "last_failure_kind": progress.last_failure_kind,
            "last_failure_fingerprint": progress.last_failure_fingerprint,
            "quarantine_candidate_failures": progress.quarantine_candidate_failures,
            "last_archived_message_id": progress.last_archived_message_id,
            "last_archived_at": progress.last_archived_at.isoformat()
            if progress.last_archived_at
            else None,
        }
        for progress, case_no in rows
    ]
    return {"total_failing": int(total_failing or 0), "items": items}


@router.get("/archivist-quarantines")
async def get_archivist_quarantines(
    session: AsyncSession = Depends(get_session),
    status: str = Query(ArchivistQuarantine.STATUS_QUARANTINED),
    limit: int = Query(50, ge=1, le=500),
):
    """查看被档案官跳过的确定性毒消息；记录在成功恢复后仍保留。"""
    if status not in {
        ArchivistQuarantine.STATUS_QUARANTINED,
        ArchivistQuarantine.STATUS_REPLAYED,
    }:
        raise HTTPException(status_code=400, detail="invalid quarantine status")
    rows = list(
        await session.scalars(
            select(ArchivistQuarantine)
            .where(ArchivistQuarantine.status == status)
            .order_by(ArchivistQuarantine.quarantined_at.desc())
            .limit(limit)
        )
    )
    total = await session.scalar(
        select(func.count(ArchivistQuarantine.id)).where(
            ArchivistQuarantine.status == status
        )
    )
    return {
        "status": status,
        "total": int(total or 0),
        "items": [
            {
                "id": item.id,
                "service_case_id": item.service_case_id,
                "message_id": item.message_id,
                "failure_fingerprint": item.failure_fingerprint,
                "failure_reason": item.failure_reason,
                "quarantined_at": item.quarantined_at.isoformat(),
                "replay_attempts": item.replay_attempts,
                "replayed_at": (
                    item.replayed_at.isoformat() if item.replayed_at else None
                ),
                "last_replay_error": item.last_replay_error,
            }
            for item in rows
        ],
    }


@router.post("/archivist-quarantines/{quarantine_id}/replay")
async def replay_archivist_quarantined_message(
    quarantine_id: int,
    session: AsyncSession = Depends(get_session),
):
    """人工原位重放一条隔离消息，不回退正式水位线。"""
    from app.services.archivist.trigger import replay_archivist_quarantine

    try:
        result = await replay_archivist_quarantine(
            session,
            quarantine_id=quarantine_id,
        )
        await session.commit()
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        quarantine = await session.get(
            ArchivistQuarantine,
            quarantine_id,
            with_for_update=True,
        )
        if quarantine is not None:
            quarantine.replay_attempts = int(quarantine.replay_attempts or 0) + 1
            quarantine.last_replay_error = f"{type(exc).__name__}: {exc}"[:2000]
            await session.commit()
        raise HTTPException(
            status_code=503,
            detail="archivist quarantine replay failed; record kept for retry",
        ) from exc
    return {
        "id": quarantine_id,
        "status": ArchivistQuarantine.STATUS_REPLAYED,
        "watermark_to": result.watermark_to,
        "delta_count": result.delta_count,
    }


@router.get("/summary")
async def get_diagnostics_summary(
    session: AsyncSession = Depends(get_session),
    probe_ai: bool = Query(
        True,
        description="是否真实调用一次 LLM 测量延迟；数据看板加载时传 false 只查数据库，避免每次进页面都产生真实调用费用。",
    ),
):
    database = {
        "status": "failed",
        "message": "数据库探针未通过",
    }
    ai_probe = {
        "status": "failed",
        "message": "AI 探针未通过",
        "latency_ms": None,
        "model": None,
    }

    try:
        await session.execute(text("SELECT 1"))
        database = {
            "status": "connected",
            "message": "SQLAlchemy + PostgreSQL 连接正常",
        }
    except Exception as exc:
        database = {
            "status": "failed",
            "message": f"数据库探针失败: {str(exc)[:120]}",
        }

    settings = get_settings()
    ai_probe["model"] = settings.ai_model
    if not probe_ai:
        ai_probe = {
            "status": "idle",
            "message": "未测速：点击按钮才会真实调用一次大模型测量延迟",
            "latency_ms": None,
            "model": settings.ai_model,
        }
    elif not settings.ai_api_key:
        ai_probe = {
            "status": "not_configured",
            "message": "未配置 AI_API_KEY，未执行连通探针",
            "latency_ms": None,
            "model": settings.ai_model,
        }
    else:
        service = AIService(settings=settings)
        started = time.perf_counter()
        try:
            reply = await asyncio.wait_for(
                service.generate_text(
                    scenario="diagnostics_probe",
                    system_prompt="你是系统健康检查助手。请只回复 OK。",
                    user_prompt="health check",
                ),
                timeout=min(max(settings.ai_timeout_seconds, 3), 15),
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            ai_probe = {
                "status": "connected" if (reply or "").strip() else "failed",
                "message": "AI 连通探针完成" if (reply or "").strip() else "AI 返回为空",
                "latency_ms": elapsed_ms,
                "model": settings.ai_model,
            }
        except Exception as exc:
            ai_probe = {
                "status": "failed",
                "message": f"AI 探针失败: {str(exc)[:120]}",
                "latency_ms": None,
                "model": settings.ai_model,
            }
        finally:
            await service.close()

    diagnostic_costs = await _diagnostic_cost_summary(session)
    return {
        "database": database,
        "ai_probe": ai_probe,
        "diagnostic_costs": diagnostic_costs,
        "checked_at": int(time.time()),
    }


async def _diagnostic_cost_summary(session: AsyncSession) -> dict:
    latest_run_id = await session.scalar(
        select(AIUsageLog.diagnostic_run_id)
        .where(
            AIUsageLog.is_diagnostic.is_(True),
            AIUsageLog.diagnostic_run_id.is_not(None),
            AIUsageLog.diagnostic_run_id != "diagnostics-probe",
        )
        .order_by(AIUsageLog.created_at.desc())
        .limit(1)
    )
    total = await _diagnostic_cost_row(session)
    latest = await _diagnostic_cost_row(session, run_id=latest_run_id) if latest_run_id else {
        "calls": 0,
        "tokens": 0,
        "estimated_cost_yuan": 0.0,
    }
    return {"latest_run_id": latest_run_id, "latest": latest, "lifetime": total}


async def _diagnostic_cost_row(session: AsyncSession, *, run_id: str | None = None) -> dict:
    statement = select(
        func.count(AIUsageLog.id),
        func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
        func.coalesce(func.sum(AIUsageLog.estimated_cost_yuan), 0.0),
    ).where(AIUsageLog.is_diagnostic.is_(True))
    if run_id:
        statement = statement.where(AIUsageLog.diagnostic_run_id == run_id)
    calls, tokens, cost = (await session.execute(statement)).one()
    return {
        "calls": int(calls or 0),
        "tokens": int(tokens or 0),
        "estimated_cost_yuan": round(float(cost or 0), 4),
    }


def _backend_dir() -> Path:
    current_file_dir = Path(__file__).resolve().parent
    return current_file_dir.parents[3]


HISTORY_FILE = "diagnostics_history.json"


def _history_path() -> Path:
    configured = os.getenv("DIAGNOSTICS_HISTORY_PATH", "").strip()
    if configured:
        return Path(configured)
    return _backend_dir() / HISTORY_FILE


def _load_history() -> dict:
    history_path = _history_path()
    if history_path.exists():
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                data = _normalize_history(data)
                changed = _clear_stale_active_run(data)
                if changed:
                    _save_history(data)
                return data
        except Exception:
            pass
    return _normalize_history({})


def _normalize_history(data: dict) -> dict:
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("statuses"), dict):
        data["statuses"] = {}
    if not isinstance(data.get("durations"), dict):
        data["durations"] = {}
    if not data.get("last_run_time"):
        data["last_run_time"] = "尚未运行"
    if not isinstance(data.get("console_logs"), dict):
        if isinstance(data.get("console_logs"), list):
            data["console_logs"] = {"all": data["console_logs"]}
        else:
            data["console_logs"] = {}
    data.setdefault("active_run", None)
    data["e2e_progress"] = _extract_e2e_progress(data.get("console_logs", {}))
    return data


def _clear_stale_active_run(history: dict) -> bool:
    active_run = history.get("active_run")
    if not isinstance(active_run, dict):
        return False
    suite_id = str(active_run.get("suite_id") or "")
    started_at = _int_or_none(active_run.get("started_at")) or 0
    mode = str(active_run.get("mode") or "")
    timeout = REAL_E2E_ACTIVE_RUN_TIMEOUT_SECONDS if mode == "real_e2e" else HEALTH_ACTIVE_RUN_TIMEOUT_SECONDS
    stale_after_restart = not _DIAGNOSTICS_RUN_LOCK.locked()
    stale_by_timeout = bool(started_at and time.time() - started_at > timeout)
    if not stale_after_restart and not stale_by_timeout:
        return False
    history["active_run"] = None
    if suite_id and history.get("statuses", {}).get(suite_id) == "running":
        history["statuses"][suite_id] = "failed"
    if mode == "health":
        for key, status in list(history.get("statuses", {}).items()):
            if status == "running":
                history["statuses"][key] = "failed"
    message = (
        f"⚠️ 检测到陈旧诊断运行状态，已自动重置 active_run"
        f"（suite={suite_id or 'unknown'}，reason={'timeout' if stale_by_timeout else 'backend_restart'}）。"
    )
    _append_console_log(history, "all", message)
    if suite_id:
        _append_console_log(history, suite_id, message)
    return True


def _empty_history() -> dict:
    return {
        "statuses": {},
        "durations": {},
        "last_run_time": "尚未运行",
        "console_logs": {},
        "active_run": None,
        "e2e_progress": _extract_e2e_progress({}),
    }


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _append_console_log(history: dict, key: str, line: str) -> None:
    logs = history.setdefault("console_logs", {}).setdefault(key, [])
    logs.append(line.rstrip("\r\n"))
    if len(logs) > MAX_CONSOLE_LOG_LINES:
        del logs[:-MAX_CONSOLE_LOG_LINES]


def _append_suite_log(history: dict, *, run_key: str, suite_id: str, line: str, mirror_to_all: bool = False) -> None:
    _append_console_log(history, run_key, line)
    if run_key == "all":
        _append_console_log(history, suite_id, line)
    elif mirror_to_all:
        _append_console_log(history, "all", line)


def _extract_e2e_progress(console_logs: dict) -> dict:
    logs = list(console_logs.get("e2e_01") or console_logs.get("all") or [])
    started_by_key: dict[str, dict] = {}
    key_by_name: dict[str, str] = {}
    completed_by_key: dict[str, dict] = {}
    reviewed_by_key: dict[str, dict] = {}
    current_key = ""
    current_day = None
    for raw_line in logs:
        line = str(raw_line)
        match = re.search(r"启动人设旅程:\s*(?P<name>[^/]+?)\s*/\s*(?P<key>[^，,\s]+)", line)
        if match:
            name = match.group("name").strip()
            key = match.group("key").strip()
            if "{" in name or "}" in name or "{" in key or "}" in key:
                continue
            current_key = key
            entry = started_by_key.setdefault(key, {"key": key, "name": name, "status": "running"})
            entry["name"] = name
            key_by_name[name] = key
            continue
        match = re.search(r"^tests/test_e2e_01_real_llm\.py::test_real_llm_persona_journey\[(?P<key>[^\]]+)\]\s*$", line)
        if match:
            key = match.group("key").strip()
            started_by_key.setdefault(key, {"key": key, "name": _persona_name(key), "status": "running"})
            if key not in completed_by_key and key not in reviewed_by_key:
                current_key = key
            continue
        match = re.search(r"\[E2E-01\]\s*(?P<name>.+?)\s*推进到 Day\s*(?P<day>\d+)", line)
        if match:
            name = match.group("name").strip()
            current_day = _int_or_none(match.group("day"))
            current_key = key_by_name.get(name, current_key)
            continue
        match = re.search(r"\[内容质量综合评审\]\s*(?P<name>[^:：]+)[:：]\s*(?P<scores>.+)", line)
        if match:
            name = match.group("name").strip()
            key = key_by_name.get(name) or current_key or name
            entry = started_by_key.setdefault(key, {"key": key, "name": name})
            entry["name"] = _persona_name(key, name)
            entry["status"] = "reviewed"
            entry["scores_text"] = match.group("scores").strip()
            reviewed_by_key[key] = dict(entry)
            continue
        match = re.search(r"\[28天用户旅程验证\]\s*(?P<name>[^:：]+)[:：]\s*(?P<summary>.+)", line)
        if match:
            name = match.group("name").strip()
            key = key_by_name.get(name) or current_key or name
            entry = started_by_key.setdefault(key, {"key": key, "name": name})
            entry["name"] = _persona_name(key, name)
            entry["status"] = "reviewed"
            entry["summary_text"] = match.group("summary").strip()
            reviewed_by_key[key] = dict(entry)
            continue
        match = re.search(r"test_real_llm_persona_journey\[(?P<key>[^\]]+)\]\s+(?P<status>PASSED|FAILED)", line)
        if match:
            key = match.group("key").strip()
            status = "passed" if match.group("status") == "PASSED" else "failed"
            entry = dict(started_by_key.get(key, {"key": key, "name": _persona_name(key)}))
            entry["name"] = _persona_name(key, entry.get("name"))
            entry["status"] = status
            completed_by_key[key] = entry
            if current_key == key:
                current_key = ""
            continue
        match = re.search(r"FAILED\s+tests/test_e2e_01_real_llm\.py::test_real_llm_persona_journey\[(?P<key>[^\]]+)\]", line)
        if match:
            key = match.group("key").strip()
            entry = dict(started_by_key.get(key, {"key": key, "name": _persona_name(key)}))
            entry["name"] = _persona_name(key, entry.get("name"))
            entry["status"] = "failed"
            completed_by_key[key] = entry
            if current_key == key:
                current_key = ""
            continue
    completed = list({**reviewed_by_key, **completed_by_key}.values())
    current = (
        started_by_key.get(current_key)
        if current_key and current_key not in completed_by_key and current_key not in reviewed_by_key
        else None
    )
    return {
        "total": len(REAL_E2E_PERSONA_KEYS),
        "completed": completed,
        "current_persona": current.get("key") if current else "",
        "current_persona_name": current.get("name") if current else "",
        "current_day": current_day,
        "passed": sum(1 for item in completed if item.get("status") == "passed"),
        "failed": sum(1 for item in completed if item.get("status") == "failed"),
        "reviewed": sum(1 for item in completed if item.get("status") == "reviewed"),
}


PERSONA_DISPLAY_NAMES = {
    "sunny_cooperator": "林晴",
    "anxious_questioner": "周宁",
    "quiet_noncompliant": "陈默",
    "shift_worker": "何夜",
    "caffeine_sensitive": "许敏",
    "plateau_frustrated": "赵稳",
    "refund_complaint": "吴退",
    "medical_caution": "顾安",
    "positive_renewal": "宋续",
    "low_mood_recovery": "沈心",
}


def _persona_name(key: str, fallback: str | None = None) -> str:
    return PERSONA_DISPLAY_NAMES.get(key, fallback or key)


REAL_E2E_PERSONA_KEYS = (
    "sunny_cooperator",
    "anxious_questioner",
    "quiet_noncompliant",
    "shift_worker",
    "caffeine_sensitive",
    "plateau_frustrated",
    "refund_complaint",
    "medical_caution",
    "positive_renewal",
    "low_mood_recovery",
)


def _save_history(history: dict):
    history_path = _history_path()
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        for key, logs in list(history.get("console_logs", {}).items()):
            if isinstance(logs, list) and len(logs) > MAX_CONSOLE_LOG_LINES:
                history["console_logs"][key] = logs[-MAX_CONSOLE_LOG_LINES:]
        history["e2e_progress"] = _extract_e2e_progress(history.get("console_logs", {}))
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


@router.get("/history")
async def get_diagnostics_history():
    """获取上一次诊断自测的完整持久化历史记录（供多浏览器/多设备实时共享同步）"""
    return _load_history()


@router.post("/clear-history")
async def clear_diagnostics_history():
    """清空持久化的测试历史记录并重置测试状态"""
    history = _empty_history()
    _save_history(history)
    return {"status": "ok"}


@router.post("/reset-active-run")
async def reset_active_run():
    """Manually clear a stale diagnostics active run without deleting history."""
    history = _load_history()
    history["active_run"] = None
    for suite_id, status in list(history.get("statuses", {}).items()):
        if status == "running":
            history["statuses"][suite_id] = "failed"
    _append_console_log(history, "all", "⚠️ 已手动重置卡住的 active_run。")
    _save_history(history)
    return {"status": "reset"}


@router.get("/run/{test_id}")
async def run_diagnostics(
    test_id: str,
    confirm_real_e2e: bool = Query(default=False),
):
    """
    运行特定的诊断测试套件，并以流（StreamingResponse）的形式实时回传控制台打印。
    同时将运行耗时、状态及日志实时同步至后端文件系统，支持跨终端共享。
    """
    # 验证 test_id
    if test_id != "all" and not any(suite["id"] == test_id for suite in TEST_SUITES):
        raise HTTPException(status_code=400, detail=f"Invalid test suite ID: {test_id}")
    selected_suite = None if test_id == "all" else next(s for s in TEST_SUITES if s["id"] == test_id)
    if selected_suite and selected_suite["requires_confirmation"] and not confirm_real_e2e:
        raise HTTPException(
            status_code=409,
            detail="该测试会调用真实模型、耗时较长；业务测试数据会回滚，仅保留诊断费用与运行记录。请确认后再运行。",
        )
    if _DIAGNOSTICS_RUN_LOCK.locked():
        raise HTTPException(status_code=409, detail="已有诊断测试正在运行，请等待其完成后再试。")
    await _DIAGNOSTICS_RUN_LOCK.acquire()

    async def run_test_generator():
        history = _load_history()
        
        suites_to_run = []
        if test_id == "all":
            suites_to_run = HEALTH_TEST_SUITES
        else:
            suites_to_run = [selected_suite]
            
        for suite in suites_to_run:
            history["statuses"][suite["id"]] = "running"
            
        # 使用世界协调时间戳，避免容器时区(UTC)与宿主机(UTC+8)的8小时偏差。前端拉取时会根据浏览器本地时区自动渲染
        history["last_run_time"] = int(time.time())
        
        if "console_logs" not in history or not isinstance(history["console_logs"], dict):
            history["console_logs"] = {}
            
        # 清除本次运行套件的专属日志
        history["console_logs"][test_id] = []
        if test_id == "all":
            for suite in suites_to_run:
                history["console_logs"][suite["id"]] = []
        
        _save_history(history)

        # 生成精美的开屏自测信息横幅
        if test_id == "all":
            start_banner = (
                "======================================================================\n"
                "🚀 [健康自测启动] 安全诊断套件 (01-08) 串联执行中...\n"
                "📦 测试范围: 使用 Mock AI/通道，测试数据回滚；不运行真实模型 E2E-01\n"
                "======================================================================\n"
            )
        else:
            suite = suites_to_run[0]
            data_note = (
                "模拟客户业务数据回滚；真实模型 token、费用与耗时作为诊断记录单独保留"
                if suite["tier"] == "real_e2e"
                else "使用 Mock AI/通道，测试数据回滚"
            )
            start_banner = (
                "======================================================================\n"
                f"📋 [系统自测启动] 用例 {suite['display_id']}: {suite['name']}\n"
                f"📝 描述信息: {suite['description']}\n"
                f"⚙️ 物理执行文件: {suite['file']}\n"
                f"📦 数据策略: {data_note}\n"
                "======================================================================\n"
            )
            
        yield start_banner
        
        history["console_logs"][test_id].append(start_banner.rstrip("\n"))
        if test_id == "all":
            for suite in suites_to_run:
                history["console_logs"][suite["id"]].append(start_banner.rstrip("\n"))
        elif suites_to_run and suites_to_run[0]["tier"] == "real_e2e":
            _append_console_log(history, "all", start_banner)

        backend_dir = _backend_dir()
        has_overall_error = False

        for suite in suites_to_run:
            history["active_run"] = {
                "mode": "health" if test_id == "all" else suite["tier"],
                "suite_id": suite["id"],
                "suite_name": suite["name"],
                "started_at": int(time.time()),
            }
            active_msg = f"\n▶ 当前执行套件: {suite['display_id']} {suite['name']}\n"
            yield active_msg
            history["console_logs"][test_id].append(active_msg.rstrip("\n"))
            if test_id == "all":
                history["console_logs"][suite["id"]].append(active_msg.rstrip("\n"))
            elif suite["tier"] == "real_e2e":
                _append_console_log(history, "all", active_msg)
            _save_history(history)

            test_file = suite["file"]
            cmd_msg = f"\n🚀 执行命令: uv run pytest {test_file} -v -s\n"
            yield cmd_msg
            
            history["console_logs"][test_id].append(cmd_msg.rstrip("\n"))
            if test_id == "all":
                history["console_logs"][suite["id"]].append(cmd_msg.rstrip("\n"))
            elif suite["tier"] == "real_e2e":
                _append_console_log(history, "all", cmd_msg)

            divider = "--------------------------------------------------\n"
            yield divider
            
            history["console_logs"][test_id].append(divider.rstrip("\n"))
            if test_id == "all":
                history["console_logs"][suite["id"]].append(divider.rstrip("\n"))
            elif suite["tier"] == "real_e2e":
                _append_console_log(history, "all", divider)

            test_path = backend_dir / test_file
            if not test_path.exists():
                err_msg = f"💥 测试文件不存在: {test_file}\n"
                yield err_msg
                
                history["console_logs"][test_id].append(err_msg.rstrip("\n"))
                if test_id == "all":
                    history["console_logs"][suite["id"]].append(err_msg.rstrip("\n"))
                elif suite["tier"] == "real_e2e":
                    _append_console_log(history, "all", err_msg)

                fail_msg = "🔴 测试失败: 诊断清单与当前仓库测试文件不一致\n"
                yield fail_msg
                
                history["console_logs"][test_id].append(fail_msg.rstrip("\n"))
                if test_id == "all":
                    history["console_logs"][suite["id"]].append(fail_msg.rstrip("\n"))
                elif suite["tier"] == "real_e2e":
                    _append_console_log(history, "all", fail_msg)

                history["statuses"][suite["id"]] = "failed"
                has_overall_error = True
                _save_history(history)
                continue

            start_time = time.perf_counter()
            # 使用 PYTHONUNBUFFERED=1 保证输出无缓冲，实时流式返回
            cmd = ["uv", "run", "pytest", test_file, "-v", "-s"]
            process = None
            try:
                run_id = (
                    f"e2e-{int(time.time())}-{secrets.token_hex(4)}"
                    if suite["tier"] == "real_e2e"
                    else ""
                )
                process_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
                if run_id:
                    process_env["RAN_AI_DIAGNOSTIC_RUN_ID"] = run_id
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(backend_dir),
                    env=process_env,
                )

                heartbeat_interval = 20 if suite["tier"] == "real_e2e" else 60
                while True:
                    try:
                        line_bytes = await asyncio.wait_for(
                            process.stdout.readline(),
                            timeout=heartbeat_interval,
                        )
                    except asyncio.TimeoutError:
                        if process.returncode is None:
                            heartbeat = (
                                f"⏳ {suite['display_id']} 仍在运行，"
                                f"已等待 {time.perf_counter() - start_time:.0f}s；"
                                "真实模型调用可能需要较长时间，请勿重复点击。\n"
                            )
                            yield heartbeat
                            history["console_logs"][test_id].append(heartbeat.rstrip("\r\n"))
                            if test_id == "all":
                                history["console_logs"][suite["id"]].append(heartbeat.rstrip("\r\n"))
                            elif suite["tier"] == "real_e2e":
                                _append_console_log(history, "all", heartbeat)
                            _save_history(history)
                            continue
                        break
                    if not line_bytes:
                        break
                    line_str = line_bytes.decode("utf-8")
                    yield line_str
                    
                    history["console_logs"][test_id].append(line_str.rstrip("\r\n"))
                    if test_id == "all":
                        history["console_logs"][suite["id"]].append(line_str.rstrip("\r\n"))
                    elif suite["tier"] == "real_e2e":
                        _append_console_log(history, "all", line_str)

                await process.wait()
                
                elapsed = f"{time.perf_counter() - start_time:.2f}s"
                history["durations"][suite["id"]] = elapsed

                if process.returncode == 0:
                    success_msg = (
                        f"\n==================================================\n"
                        f"🟢 测试成功: {test_file} 顺利通过!\n"
                        f"⏱️ 实际运行耗时: {elapsed}\n"
                        f"==================================================\n"
                    )
                    yield success_msg
                    
                    history["console_logs"][test_id].append(success_msg.rstrip("\n"))
                    if test_id == "all":
                        history["console_logs"][suite["id"]].append(success_msg.rstrip("\n"))
                    elif suite["tier"] == "real_e2e":
                        _append_console_log(history, "all", success_msg)
                    history["statuses"][suite["id"]] = "passed"
                else:
                    fail_msg = (
                        f"\n==================================================\n"
                        f"🔴 测试失败: {test_file} 发生异常!\n"
                        f"⏱️ 实际运行耗时: {elapsed}\n"
                        f"❌ 运行退出代码: {process.returncode}\n"
                        f"==================================================\n"
                    )
                    yield fail_msg
                    
                    history["console_logs"][test_id].append(fail_msg.rstrip("\n"))
                    if test_id == "all":
                        history["console_logs"][suite["id"]].append(fail_msg.rstrip("\n"))
                    elif suite["tier"] == "real_e2e":
                        _append_console_log(history, "all", fail_msg)
                    history["statuses"][suite["id"]] = "failed"
                    has_overall_error = True
            except asyncio.CancelledError:
                if process is not None and process.returncode is None:
                    process.terminate()
                    await process.wait()
                raise
            except FileNotFoundError:
                err_msg = "\n💥 运行异常: 容器内未安装 pytest，请先为 backend 运行环境安装测试依赖。\n"
                yield err_msg
                
                history["console_logs"][test_id].append(err_msg.rstrip("\n"))
                if test_id == "all":
                    history["console_logs"][suite["id"]].append(err_msg.rstrip("\n"))
                elif suite["tier"] == "real_e2e":
                    _append_console_log(history, "all", err_msg)
                history["statuses"][suite["id"]] = "failed"
                has_overall_error = True
            except Exception as e:
                err_msg = f"\n💥 运行异常: 无法启动 pytest 进程 - {str(e)}\n"
                yield err_msg
                
                history["console_logs"][test_id].append(err_msg.rstrip("\n"))
                if test_id == "all":
                    history["console_logs"][suite["id"]].append(err_msg.rstrip("\n"))
                elif suite["tier"] == "real_e2e":
                    _append_console_log(history, "all", err_msg)
                history["statuses"][suite["id"]] = "failed"
                has_overall_error = True

            _save_history(history)

        end_banner = "\n=== [SYSTEM DIAGNOSTICS END] ===\n"
        yield end_banner
        history["active_run"] = None
        
        history["console_logs"][test_id].append(end_banner.strip())
        if test_id == "all":
            for s in suites_to_run:
                history["console_logs"][s["id"]].append(end_banner.strip())
        elif suites_to_run and suites_to_run[0]["tier"] == "real_e2e":
            _append_console_log(history, "all", end_banner)
        
        # 追加最终整体总结日志
        summary_divider = "======================================================================\n"
        history["console_logs"][test_id].append(summary_divider.strip())
        if test_id == "all":
            for s in suites_to_run:
                history["console_logs"][s["id"]].append(summary_divider.strip())
        elif suites_to_run and suites_to_run[0]["tier"] == "real_e2e":
            _append_console_log(history, "all", summary_divider)
        
        if test_id == "all":
            overall_msg = (
                "🔴 健康自测执行完毕: 发现异常模块，请检查上方终端输出日志！\n"
                if has_overall_error
                else "🟢 健康自测顺利通过；真实模型 E2E-01 未运行。\n"
            )
        else:
            overall_msg = (
                "🔴 单项诊断执行完毕: 发现异常，请检查上方日志。\n"
                if has_overall_error
                else f"🟢 单项诊断 {suites_to_run[0]['display_id']} 顺利通过。\n"
            )
        history["console_logs"][test_id].append(overall_msg.strip())
        if test_id == "all":
            for s in suites_to_run:
                history["console_logs"][s["id"]].append(overall_msg.strip())
        elif suites_to_run and suites_to_run[0]["tier"] == "real_e2e":
            _append_console_log(history, "all", overall_msg)
        
        history["console_logs"][test_id].append(summary_divider.strip())
        if test_id == "all":
            for s in suites_to_run:
                history["console_logs"][s["id"]].append(summary_divider.strip())
        elif suites_to_run and suites_to_run[0]["tier"] == "real_e2e":
            _append_console_log(history, "all", summary_divider)
        
        _save_history(history)

    async def guarded_test_generator():
        try:
            async for chunk in run_test_generator():
                yield chunk
        finally:
            history = _load_history()
            history["active_run"] = None
            for suite_id, status in list(history.get("statuses", {}).items()):
                if status == "running":
                    history["statuses"][suite_id] = "failed"
            _save_history(history)
            _DIAGNOSTICS_RUN_LOCK.release()

    return StreamingResponse(
        guarded_test_generator(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no", # 禁用 Nginx 等反向代理的缓冲区
            "Connection": "keep-alive",
        }
    )
