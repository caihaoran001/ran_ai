from __future__ import annotations

import asyncio
import os
import time
import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.services.ai_service import AIService

router = APIRouter()

TEST_SUITES = [
    {
        "id": "01",
        "name": "test_01_intake",
        "description": "入营问卷基本录入与数据合法性校验",
        "file": "tests/test_01_intake.py",
    },
    {
        "id": "02",
        "name": "test_02_chat",
        "description": "社群模拟群聊多轮对话与情绪/健康风控拦截测试",
        "file": "tests/test_02_chat.py",
    },
    {
        "id": "03",
        "name": "test_03_checkin",
        "description": "打卡数据创建、解析结构和排序校验测试",
        "file": "tests/test_03_checkin.py",
    },
    {
        "id": "04",
        "name": "test_04_stage_reports",
        "description": "阶段报告与毕业报告自动生成测试",
        "file": "tests/test_04_stage_reports.py",
    },
    {
        "id": "05",
        "name": "test_05_evolution",
        "description": "24小时群聊沉淀 FAQ 知识进化与打卡校验测试",
        "file": "tests/test_05_evolution.py",
    },
    {
        "id": "06",
        "name": "test_06_toggles",
        "description": "策略开关、知识进化配置与软删除校验测试",
        "file": "tests/test_06_toggles.py",
    },
    {
        "id": "07",
        "name": "test_07_real_llm_e2e",
        "description": "真实大模型全链路 E2E 冒烟测试（含 24 轮真实多期陪伴对话、RAG 自进化闭环与案例反哺）",
        "file": "tests/test_07_real_llm_e2e.py",
    },
    {
        "id": "08",
        "name": "test_08_qiwe_channel",
        "description": "QiWe 通道代码逻辑测试：消息聚合、去重、异常处理（使用 mock 对象，不测试真实通道）",
        "file": "tests/test_08_qiwe_channel.py",
    },
    {
        "id": "09",
        "name": "test_09_outcome_engine",
        "description": "成效引擎测试：LLM 提取 + 阶段状态更新 + 行动焦点评分 + 时间感知",
        "file": "tests/test_09_outcome_engine.py",
    },
]

@router.get("/test-suites")
async def get_test_suites():
    """获取所有系统自测套件的元数据"""
    return TEST_SUITES


@router.get("/summary")
async def get_diagnostics_summary(
    session: AsyncSession = Depends(get_session),
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
    if not settings.ai_api_key:
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

    return {
        "database": database,
        "ai_probe": ai_probe,
        "checked_at": int(time.time()),
    }


def _backend_dir() -> Path:
    current_file_dir = Path(__file__).resolve().parent
    return current_file_dir.parents[3]


HISTORY_FILE = "diagnostics_history.json"


def _load_history() -> dict:
    history_path = _backend_dir() / HISTORY_FILE
    if history_path.exists():
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 兼容旧版本列表格式，平滑迁移
                if not isinstance(data.get("console_logs"), dict):
                    if isinstance(data.get("console_logs"), list):
                        data["console_logs"] = {"all": data["console_logs"]}
                    else:
                        data["console_logs"] = {}
                return data
        except Exception:
            pass
    return {
        "statuses": {},
        "durations": {},
        "last_run_time": "尚未运行",
        "console_logs": {}
    }


def _save_history(history: dict):
    history_path = _backend_dir() / HISTORY_FILE
    try:
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
    history = {
        "statuses": {},
        "durations": {},
        "last_run_time": "尚未运行",
        "console_logs": {}
    }
    _save_history(history)
    return {"status": "ok"}


@router.get("/run/{test_id}")
async def run_diagnostics(test_id: str):
    """
    运行特定的诊断测试套件，并以流（StreamingResponse）的形式实时回传控制台打印。
    同时将运行耗时、状态及日志实时同步至后端文件系统，支持跨终端共享。
    """
    # 验证 test_id
    if test_id != "all" and not any(suite["id"] == test_id for suite in TEST_SUITES):
        raise HTTPException(status_code=400, detail=f"Invalid test suite ID: {test_id}")

    async def run_test_generator():
        history = _load_history()
        
        suites_to_run = []
        if test_id == "all":
            suites_to_run = TEST_SUITES
        else:
            suites_to_run = [next(s for s in TEST_SUITES if s["id"] == test_id)]
            
        for suite in suites_to_run:
            history["statuses"][suite["id"]] = "running"
            
        # 使用世界协调时间戳，避免容器时区(UTC)与宿主机(UTC+8)的8小时偏差。前端拉取时会根据浏览器本地时区自动渲染
        history["last_run_time"] = int(time.time())
        
        if "console_logs" not in history or not isinstance(history["console_logs"], dict):
            history["console_logs"] = {}
            
        # 清除本次运行套件的专属日志
        history["console_logs"][test_id] = []
        if test_id == "all":
            for suite in TEST_SUITES:
                history["console_logs"][suite["id"]] = []
        
        _save_history(history)

        # 生成精美的开屏自测信息横幅
        if test_id == "all":
            start_banner = (
                "======================================================================\n"
                "🚀 [全量自测启动] 自动化冒烟测试 (01 ~ 07) 串联执行中...\n"
                "📦 测试范围: 包含用户入营、社群聊天风控、打卡、阶段总结、知识进化等全链路\n"
                "======================================================================\n"
            )
        else:
            suite = suites_to_run[0]
            start_banner = (
                "======================================================================\n"
                f"📋 [系统自测启动] 用例 {suite['id']}: {suite['name']}\n"
                f"📝 描述信息: {suite['description']}\n"
                f"⚙️ 物理执行文件: {suite['file']}\n"
                "======================================================================\n"
            )
            
        yield start_banner
        
        history["console_logs"][test_id].append(start_banner.rstrip("\n"))
        if test_id == "all":
            for suite in TEST_SUITES:
                history["console_logs"][suite["id"]].append(start_banner.rstrip("\n"))

        backend_dir = _backend_dir()
        has_overall_error = False

        for suite in suites_to_run:
            test_file = suite["file"]
            cmd_msg = f"\n🚀 执行命令: uv run pytest {test_file} -v -s\n"
            yield cmd_msg
            
            history["console_logs"][test_id].append(cmd_msg.rstrip("\n"))
            if test_id == "all":
                history["console_logs"][suite["id"]].append(cmd_msg.rstrip("\n"))

            divider = "--------------------------------------------------\n"
            yield divider
            
            history["console_logs"][test_id].append(divider.rstrip("\n"))
            if test_id == "all":
                history["console_logs"][suite["id"]].append(divider.rstrip("\n"))

            test_path = backend_dir / test_file
            if not test_path.exists():
                err_msg = f"💥 测试文件不存在: {test_file}\n"
                yield err_msg
                
                history["console_logs"][test_id].append(err_msg.rstrip("\n"))
                if test_id == "all":
                    history["console_logs"][suite["id"]].append(err_msg.rstrip("\n"))

                fail_msg = "🔴 测试失败: 诊断清单与当前仓库测试文件不一致\n"
                yield fail_msg
                
                history["console_logs"][test_id].append(fail_msg.rstrip("\n"))
                if test_id == "all":
                    history["console_logs"][suite["id"]].append(fail_msg.rstrip("\n"))

                history["statuses"][suite["id"]] = "failed"
                has_overall_error = True
                _save_history(history)
                continue

            start_time = time.perf_counter()
            # 使用 PYTHONUNBUFFERED=1 保证输出无缓冲，实时流式返回
            cmd = ["uv", "run", "pytest", test_file, "-v", "-s"]
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(backend_dir),
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )

                while True:
                    line_bytes = await process.stdout.readline()
                    if not line_bytes:
                        break
                    line_str = line_bytes.decode("utf-8")
                    yield line_str
                    
                    history["console_logs"][test_id].append(line_str.rstrip("\r\n"))
                    if test_id == "all":
                        history["console_logs"][suite["id"]].append(line_str.rstrip("\r\n"))

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
                    history["statuses"][suite["id"]] = "failed"
                    has_overall_error = True
            except FileNotFoundError:
                err_msg = "\n💥 运行异常: 容器内未安装 pytest，请先为 backend 运行环境安装测试依赖。\n"
                yield err_msg
                
                history["console_logs"][test_id].append(err_msg.rstrip("\n"))
                if test_id == "all":
                    history["console_logs"][suite["id"]].append(err_msg.rstrip("\n"))
                history["statuses"][suite["id"]] = "failed"
                has_overall_error = True
            except Exception as e:
                err_msg = f"\n💥 运行异常: 无法启动 pytest 进程 - {str(e)}\n"
                yield err_msg
                
                history["console_logs"][test_id].append(err_msg.rstrip("\n"))
                if test_id == "all":
                    history["console_logs"][suite["id"]].append(err_msg.rstrip("\n"))
                history["statuses"][suite["id"]] = "failed"
                has_overall_error = True

            _save_history(history)

        end_banner = "\n=== [SYSTEM DIAGNOSTICS END] ===\n"
        yield end_banner
        
        history["console_logs"][test_id].append(end_banner.strip())
        if test_id == "all":
            for s in TEST_SUITES:
                history["console_logs"][s["id"]].append(end_banner.strip())
        
        # 追加最终整体总结日志
        summary_divider = "======================================================================\n"
        history["console_logs"][test_id].append(summary_divider.strip())
        if test_id == "all":
            for s in TEST_SUITES:
                history["console_logs"][s["id"]].append(summary_divider.strip())
        
        overall_msg = (
            "🔴 全量核心测试执行完毕: 发现异常模块，请检查上方终端输出日志！\n"
            if has_overall_error else
            "🟢 全量核心测试 100% 顺利通过！系统健康度达到完美水准！\n"
        )
        history["console_logs"][test_id].append(overall_msg.strip())
        if test_id == "all":
            for s in TEST_SUITES:
                history["console_logs"][s["id"]].append(overall_msg.strip())
        
        history["console_logs"][test_id].append(summary_divider.strip())
        if test_id == "all":
            for s in TEST_SUITES:
                history["console_logs"][s["id"]].append(summary_divider.strip())
        
        _save_history(history)

    return StreamingResponse(
        run_test_generator(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no", # 禁用 Nginx 等反向代理的缓冲区
            "Connection": "keep-alive",
        }
    )
