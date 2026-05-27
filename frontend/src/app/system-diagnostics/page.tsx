"use client";

import React, { useState, useEffect, useRef } from "react";
import Link from "next/link";
import type { DiagnosticsSummary } from "@/lib/api";

type TestSuite = {
  id: string;
  name: string;
  description: string;
  file: string;
};

type TestStatus = "idle" | "running" | "passed" | "failed";

export default function SystemDiagnosticsPage() {
  const [testSuites, setTestSuites] = useState<TestSuite[]>([]);
  const [statuses, setStatuses] = useState<Record<string, TestStatus>>({});
  const [consoleLogs, setConsoleLogs] = useState<Record<string, string[]>>({});
  const [selectedSuiteId, setSelectedSuiteId] = useState<string>("all");
  const [runningAll, setRunningAll] = useState(false);
  const [currentRunningId, setCurrentRunningId] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summary, setSummary] = useState<DiagnosticsSummary>({
    database: { status: "checking", message: "数据库探针检测中" },
    ai_probe: { status: "checking", message: "AI 探针检测中", latency_ms: null, model: null },
    checked_at: 0,
  });
  const [lastRunTime, setLastRunTime] = useState<string | number>("尚未运行");
  const [durations, setDurations] = useState<Record<string, string>>({});

  const terminalEndRef = useRef<HTMLDivElement>(null);

  // 1. 获取所有测试套件的元数据，并从后端加载历史记录
  useEffect(() => {
    async function fetchSuitesAndHistory() {
      try {
        const res = await fetch("/api/backend/diagnostics/test-suites");
        if (res.ok) {
          const data = await res.json();
          setTestSuites(data);
          
          // 从后端拉取持久化历史记录
          const historyRes = await fetch("/api/backend/diagnostics/history");
          if (historyRes.ok) {
            const history = await historyRes.json();
            
            // 恢复各个用例的状态与耗时
            const loadedStatuses: Record<string, TestStatus> = {};
            data.forEach((suite: TestSuite) => {
              loadedStatuses[suite.id] = (history.statuses?.[suite.id] || "idle") as TestStatus;
            });
            setStatuses(loadedStatuses);
            
            if (history.durations) setDurations(history.durations);
            if (history.console_logs) {
              if (Array.isArray(history.console_logs)) {
                setConsoleLogs({ all: history.console_logs });
              } else {
                setConsoleLogs(history.console_logs);
              }
            }
            if (history.last_run_time) setLastRunTime(history.last_run_time);
          } else {
            const initialStatuses: Record<string, TestStatus> = {};
            data.forEach((suite: TestSuite) => {
              initialStatuses[suite.id] = "idle";
            });
            setStatuses(initialStatuses);
          }
        }
      } catch (err) {
        console.error("加载自测套件及历史记录失败:", err);
      }
    }
    fetchSuitesAndHistory();
  }, []);

  const syncHistoryFromBackend = async () => {
    try {
      const res = await fetch("/api/backend/diagnostics/history");
      if (res.ok) {
        const history = await res.json();
        
        if (history.statuses) {
          setStatuses((prev) => {
            const nextStatuses = { ...prev };
            Object.keys(history.statuses).forEach((id) => {
              nextStatuses[id] = history.statuses[id] as TestStatus;
            });
            return nextStatuses;
          });
        }
        if (history.durations) setDurations(history.durations);
        if (history.console_logs) {
          if (Array.isArray(history.console_logs)) {
            setConsoleLogs({ all: history.console_logs });
          } else {
            setConsoleLogs(history.console_logs);
          }
        }
        if (history.last_run_time) setLastRunTime(history.last_run_time);
      }
    } catch (e) {
      console.error("同步后端自测历史失败:", e);
    }
  };

  const loadSummary = async () => {
    setSummaryLoading(true);
    try {
      const res = await fetch("/api/backend/diagnostics/summary", { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data: DiagnosticsSummary = await res.json();
      setSummary(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : "诊断摘要加载失败";
      setSummary({
        database: { status: "failed", message },
        ai_probe: { status: "failed", message, latency_ms: null, model: null },
        checked_at: 0,
      });
    } finally {
      setSummaryLoading(false);
    }
  };

  useEffect(() => {
    loadSummary();
  }, []);

  // 2. 日志自动滚动
  const activeLogs = consoleLogs[selectedSuiteId] || [];

  useEffect(() => {
    if (autoScroll && terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [activeLogs, autoScroll]);

  // 3. 运行特定单项测试
  const runSingleTest = async (suite: TestSuite) => {
    if (currentRunningId || runningAll) return;
    
    setCurrentRunningId(suite.id);
    setSelectedSuiteId(suite.id); // 自动聚焦到当前自测
    setStatuses((prev) => ({ ...prev, [suite.id]: "running" as TestStatus }));
    
    setConsoleLogs((prev) => ({
      ...prev,
      [suite.id]: [
        `🌐 [DIAGNOSTICS] 启动单项自测: ${suite.name} ...`,
        `⚙️ 执行文件: ${suite.file}`,
        `----------------------------------------------------------------------`
      ]
    }));

    try {
      const response = await fetch(`/api/backend/diagnostics/run/${suite.id}`);
      if (!response.body) {
        throw new Error("响应没有正文体");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split("\n");
        lines.forEach((line) => {
          if (line.trim()) {
            setConsoleLogs((prev) => {
              const currentLogs = prev[suite.id] || [];
              return {
                ...prev,
                [suite.id]: [...currentLogs, line]
              };
            });
            
            // 实时感知单项自测结束，以便给左侧状态灯实时打点
            if (line.includes("🟢 测试成功")) {
              setStatuses((prev) => ({ ...prev, [suite.id]: "passed" }));
            } else if (line.includes("🔴 测试失败")) {
              setStatuses((prev) => ({ ...prev, [suite.id]: "failed" }));
            }
          }
        });
      }

      await syncHistoryFromBackend();
      await loadSummary();

    } catch (error: any) {
      setStatuses((prev) => ({ ...prev, [suite.id]: "failed" as TestStatus }));
      setConsoleLogs((prev) => {
        const currentLogs = prev[suite.id] || [];
        return {
          ...prev,
          [suite.id]: [...currentLogs, `💥 运行异常: ${error.message || error}`]
        };
      });
    } finally {
      setCurrentRunningId(null);
    }
  };

  // 4. 一键跑全量测试 (test_01 到 test_07 串联)
  const runAllTests = async () => {
    if (currentRunningId || runningAll) return;

    setRunningAll(true);
    setSelectedSuiteId("all"); // 自动聚焦到全量大终端
    
    setConsoleLogs((prev) => ({
      ...prev,
      all: [
        `🚀 [SYSTEM HEALTH] 启动全量自动化冒烟测试 (01 ~ 07) ...`,
        `📦 包含 7 项核心数据库/大模型/打卡/进化链的整合验证`,
        `======================================================================`
      ]
    }));

    const newStatuses = { ...statuses };
    testSuites.forEach((s) => {
      newStatuses[s.id] = "running" as TestStatus;
    });
    setStatuses(newStatuses);

    try {
      const response = await fetch(`/api/backend/diagnostics/run/all`);
      if (!response.body) throw new Error("流式响应不可用");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split("\n");
        lines.forEach((line) => {
          if (line.trim()) {
            setConsoleLogs((prev) => {
              const currentLogs = prev.all || [];
              return {
                ...prev,
                all: [...currentLogs, line]
              };
            });
            
            // 实时感知个别用例的结束，以便给左侧状态灯实时打点
            if (line.includes("🟢 测试成功")) {
              const matched = line.match(/tests\/(test_\d+_[a-zA-Z0-9_]+)\.py/);
              if (matched) {
                const matchedFile = matched[1];
                const matchedSuite = testSuites.find(s => s.file.includes(matchedFile));
                if (matchedSuite) {
                  setStatuses(prev => ({ ...prev, [matchedSuite.id]: "passed" }));
                }
              }
            } else if (line.includes("🔴 测试失败")) {
              const matched = line.match(/tests\/(test_\d+_[a-zA-Z0-9_]+)\.py/);
              if (matched) {
                const matchedFile = matched[1];
                const matchedSuite = testSuites.find(s => s.file.includes(matchedFile));
                if (matchedSuite) {
                  setStatuses(prev => ({ ...prev, [matchedSuite.id]: "failed" }));
                }
              }
            }
          }
        });
      }

      await syncHistoryFromBackend();
      await loadSummary();
    } catch (err: any) {
      setConsoleLogs((prev) => {
        const currentLogs = prev.all || [];
        return {
          ...prev,
          all: [...currentLogs, `💥 运行异常: ${err.message || err}`]
        };
      });
      await syncHistoryFromBackend();
    } finally {
      setCurrentRunningId(null);
      setRunningAll(false);
    }
  };

  // 复制终端日志
  const copyLogs = () => {
    const text = activeLogs.join("\n");
    navigator.clipboard.writeText(text);
    alert(`[${selectedSuiteId === "all" ? "全量自测" : selectedSuiteId}] 终端日志已成功复制到剪贴板！`);
  };

  // 清空终端日志与自测历史状态（同步后端持久层）
  const clearLogs = async () => {
    try {
      await fetch("/api/backend/diagnostics/clear-history", { method: "POST" });
      setConsoleLogs({});
      const resetStatuses: Record<string, TestStatus> = {};
      testSuites.forEach((suite) => {
        resetStatuses[suite.id] = "idle";
      });
      setStatuses(resetStatuses);
      setDurations({});
      setLastRunTime("尚未运行");
      setSelectedSuiteId("all");
    } catch (e) {
      console.error("清空后端自测历史失败:", e);
    }
  };

  // 1. 定义全量自测试剂盒方法名与中文核心业务功能说明的映射字典
  const TEST_TRANSLATIONS: Record<string, string> = {
    // test_01_intake
    "test_questionnaire_submit_creates_profile_and_documents": "用户入营问卷录入，自动生成用户画像与处方交付件",
    "test_generating_status_on_submit": "提交问卷时，自动将状态锁定为'生成中'防止重复执行",
    "test_intake_facts_bmi_calculation": "入营客观事实计算器：身高体重 BMI 计算公式及精度校对",
    "test_failed_generation_reverts_status": "若交付文件大模型生成失败，系统自动回滚服务单状态防止挂死",

    // test_02_chat
    "test_normal_message_no_risk": "常规社群聊天：AI助手正常响应，不触发风控",
    "test_high_risk_chronic_disease": "风控拦截测试：涉及高危慢性病/用药安全词，系统拦截并转人工",
    "test_emotional_crisis": "情绪风控测试：检测到崩溃/极度焦虑，触发心理预警转人工",
    "test_refund_intent": "退款意向识别：识别到退款诉求，调用客服话术规制",
    "test_renewal_intent": "正面突破测试：识别到正面情绪/续营意向，触发反哺案例库检索",
    "test_ai_reply_stored_as_outbound": "应答归档存储：AI自动回复的消息以outbound发送渠道正确记录",

    // test_03_checkin
    "test_create_single_checkin": "单次打卡测试：创建打卡数据并校验卡路里及水入量等指标合法性",
    "test_seed_28_days_checkins": "时光模拟加速：批量播种生成 28 天健康陪伴打卡打分流水",
    "test_checkin_parsed_json_structure": "打卡智能解析：校验打卡文本中提取结构化打分字段的正确性",
    "test_checkin_date_ordering": "打卡排序排序：校验打卡瀑布流中时间戳逆序排列逻辑",

    // test_04_stage_reports
    "test_stage_report_generation_all_stages": "阶段评估生成：在第7/14/21/28天自动生成四期阶段审查总结",
    "test_stage_report_no_duplicate_generation": "生成幂等性校验：防止在同一评估窗口内为同一个人重复生成报告",
    "test_stage_report_content_has_checkin_data": "数据融合校验：确保阶段评估中精确提取并汇总了该阶段的打卡数据",
    "test_graduation_report": "毕业报告自动生成：终期自动生成 Word 版结营陪伴毕业报告",

    // test_05_evolution
    "test_knowledge_evolution_config_defaults": "自进化引擎测试：系统核心进化参数的加载与默认配置开关检测",
    "test_pure_extraction_with_seed_data": "案例提取测试：脱敏聊天数据，提取 FAQ 及主题分类标签",
    "test_knowledge_drafts_list": "草稿中心自测：FAQ 待审核进化草稿的展示与管理接口校验",
    "test_evolution_generates_drafts_from_seeded_chat": "知识闭环沉淀：大模型对社群聊天脱敏，自动生成待审核知识草稿",
    "test_stage_case_scan_uses_full_history_and_existing_case_context": "标杆库自进化：全量扫描聊天历史，提炼出高分标杆优秀案例草稿",

    // test_06_toggles
    "test_content_delivery_policy_crud": "策略配置开关：交付发送策略的创建/查询/修改/删除行为校验",
    "test_ai_reply_policy_crud": "应答策略开关：社群 AI 应答开关配置的管理行为校验",
    "test_service_case_auto_send_toggle": "全局开关控制：全局服务单自动派发开关逻辑响应测试",
    "test_service_case_ai_reply_toggle": "AI应答开关控制：群聊 AI 应答全局控制开关对回复链路的阻断测试",
    "test_knowledge_evolution_settings": "全局进化控制：每日定时触发 FAQ 自进化开关的读写校验",
    "test_content_delivery_policy_auto_send_behaviour": "联动拦截机制：在开关关闭时阻止处方文件触发自动派发逻辑",
    "test_soft_delete_service_case": "数据安全校验：使用 deleted_at 软删除逻辑保障业务数据的可审计可恢复性",

    // test_07_real_llm_e2e
    "test_real_llm_full_saga_end_to_end": "大模型终极终局闭环：24轮真实多期群聊、打卡、阶段审查、自进化、案例反哺全链路E2E实打冒烟测试"
  };

  // 彩色终端日志渲染辅助
  const formatLogLine = (line: string, index: number) => {
    let displayLine = line;
    let isOutcomeLine = false;
    let isPassed = false;

    // 匹配类似 tests/test_01_intake.py::test_name PASSED / FAILED / ERROR / SKIPPED
    const match = line.match(/(test_[a-zA-Z0-9_]+)\s+(PASSED|FAILED|ERROR|SKIPPED)/i);
    if (match) {
      const testMethodName = match[1];
      const outcome = match[2].toUpperCase();
      const translation = TEST_TRANSLATIONS[testMethodName];
      isOutcomeLine = true;
      isPassed = outcome === "PASSED";

      if (translation) {
        const parts = line.split("::");
        if (parts.length > 1) {
          const filePart = parts[0];
          displayLine = `${filePart} ➔ 💡 ${translation} (${testMethodName}) [ ${isPassed ? "🟢 通过" : `🔴 失败 (${outcome})`} ]`;
        } else {
          displayLine = `${line.replace(match[0], "")}${testMethodName} ➔ 💡 ${translation} [ ${isPassed ? "🟢 通过" : `🔴 失败 (${outcome})`} ]`;
        }
      }
    }

    // 根据关键字着色
    let textColor = "text-zinc-300";
    if (isOutcomeLine) {
      textColor = isPassed ? "text-emerald-400 font-semibold" : "text-rose-400 font-bold";
    } else if (line.includes("🟢 测试成功") || line.includes("🎉") || line.includes("PASSED") || line.includes("100% 成功")) {
      textColor = "text-emerald-400 font-semibold";
    } else if (line.includes("🔴 测试失败") || line.includes("FAILED") || line.includes("❌") || line.includes("💥")) {
      textColor = "text-rose-400 font-semibold";
    } else if (line.includes("🚀 执行命令") || line.includes("启动单项") || line.includes("SYSTEM HEALTH")) {
      textColor = "text-indigo-400 font-medium";
    } else if (line.includes("💬 [用户") || line.includes("🤖 [AI 助理")) {
      textColor = "text-sky-300";
    } else if (line.startsWith("===") || line.startsWith("---") || line.startsWith("=======")) {
      textColor = "text-zinc-600";
    }

    return (
      <div key={index} className={`py-0.5 leading-6 ${textColor} select-text font-mono break-all`}>
        {displayLine}
      </div>
    );
  };

  const dbStatusValue =
    summary.database.status === "connected"
      ? "CONNECTED"
      : summary.database.status === "checking"
        ? "CHECKING"
        : "FAILED";

  const aiLatencyValue =
    summary.ai_probe.status === "connected" && typeof summary.ai_probe.latency_ms === "number"
      ? `${(summary.ai_probe.latency_ms / 1000).toFixed(2)}s`
      : summary.ai_probe.status === "not_configured"
        ? "NOT CONFIGURED"
        : summary.ai_probe.status === "checking"
          ? "CHECKING"
          : "FAILED";

  const formattedLastRunTime = 
    typeof lastRunTime === "number" || (!isNaN(Number(lastRunTime)) && lastRunTime !== "尚未运行")
      ? new Date(Number(lastRunTime) * 1000).toLocaleTimeString("zh-CN", { hour12: false })
      : lastRunTime;

  return (
    <div className="space-y-8 pb-12">
      {/* 顶部标题与返回导航 */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Link 
              href="/dashboard" 
              className="text-zinc-500 hover:text-zinc-800 transition text-sm font-medium flex items-center gap-1"
            >
              ← 返回看板
            </Link>
            <span className="text-zinc-300">|</span>
            <span className="text-xs px-2.5 py-0.5 rounded-full bg-indigo-50 text-indigo-600 font-semibold border border-indigo-200/50">
              开发者专用诊断中心
            </span>
          </div>
          <h1 className="text-2xl font-bold text-zinc-900 mt-2 flex items-center gap-2">
            ⚡ 系统健康与自测诊断控制台
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            直接在网页中流式执行 01~07 核心测试套件，独立持久化保存每个套件的上一次运行历史与耗时。
          </p>
        </div>

        <button
          onClick={runAllTests}
          disabled={!!currentRunningId || runningAll}
          className={`px-5 py-2.5 rounded-xl text-white font-semibold text-sm transition shadow-lg flex items-center gap-2 ${
            !!currentRunningId || runningAll
              ? "bg-zinc-400 cursor-not-allowed"
              : "bg-indigo-600 hover:bg-indigo-700 shadow-indigo-500/20 active:translate-y-0.5"
          }`}
        >
          🚀 {runningAll ? "正在串联跑全量自测..." : "一键全量健康自测"}
        </button>
      </div>

      {/* 第一板块：系统健康度元指标 */}
      <div className="grid gap-6 md:grid-cols-4">
        <MetricCard
          title="数据库状态"
          value={dbStatusValue}
          description={summaryLoading ? "数据库探针检测中" : summary.database.message}
          color={summary.database.status === "connected" ? "green" : summary.database.status === "checking" ? "yellow" : "red"}
          icon="🛡️"
        />
        <MetricCard
          title="AI 接口探针延迟"
          value={aiLatencyValue}
          description={
            summaryLoading
              ? "AI 探针检测中"
              : summary.ai_probe.status === "connected"
                ? `${summary.ai_probe.model || "AI"} 单次连通探针`
                : summary.ai_probe.message
          }
          color={
            summary.ai_probe.status === "connected"
              ? "indigo"
              : summary.ai_probe.status === "checking"
                ? "yellow"
                : summary.ai_probe.status === "not_configured"
                  ? "blue"
                  : "red"
          }
          icon="🧠"
        />
        <MetricCard
          title="测试套件总数"
          value={`${testSuites.length} 个核心用例`}
          description="01 ~ 07 自动化流程冒烟套件"
          color="blue"
          icon="📋"
        />
        <MetricCard
          title="最近一次诊断"
          value={formattedLastRunTime}
          description="测试执行完毕的最新统计时间"
          color="yellow"
          icon="⏳"
        />
      </div>

      <div className="grid gap-8 lg:grid-cols-3">
        {/* 左侧：测试套件列表 */}
        <div className="lg:col-span-1 space-y-4">
          <div className="bg-white rounded-xl border border-zinc-200/80 p-5 shadow-sm">
            <h2 className="text-base font-bold text-zinc-900 mb-1">测试套件（01 ~ 07）</h2>
            <p className="text-xs text-zinc-500 mb-4">每个测试运行完均会物理清理/回滚，无残留污染。</p>

            <div className="space-y-3.5">
              {testSuites.map((suite) => {
                const status = statuses[suite.id] || "idle";
                const isSelected = selectedSuiteId === suite.id;
                return (
                  <div 
                    key={suite.id}
                    onClick={() => setSelectedSuiteId(suite.id)}
                    className={`p-4 rounded-xl border transition relative cursor-pointer group ${
                      isSelected
                        ? "border-indigo-500 bg-indigo-50/10 shadow-sm ring-1 ring-indigo-500/20"
                        : "border-zinc-100 hover:border-zinc-200/80 bg-zinc-50/50 hover:bg-zinc-50"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-zinc-200/60 text-zinc-600">
                          {suite.id}
                        </span>
                        <h3 className="text-sm font-bold text-zinc-800">{suite.name}</h3>
                      </div>
                      
                      {/* 状态指示灯发光 */}
                      <StatusBulb status={status} />
                    </div>

                    <p className="text-xs text-zinc-500 mt-2 leading-relaxed">
                      {suite.description}
                    </p>

                    <div className="mt-3 flex items-start justify-between gap-2">
                      <div className="flex flex-col gap-1 min-w-0">
                        <span className="text-[10px] text-zinc-400 font-mono select-all truncate block" title={suite.file}>
                          {suite.file}
                        </span>
                        {durations[suite.id] && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 mt-1 rounded-md text-[10px] font-bold font-mono bg-indigo-50/70 text-indigo-700 border border-indigo-100/40 select-none w-fit shadow-[0_1px_2px_rgba(99,102,241,0.05)] animate-fadeIn">
                            ⏱️ 耗时 <span className="text-indigo-600 font-black ml-0.5">{durations[suite.id]}</span>
                          </span>
                        )}
                      </div>
                      
                      <button
                        onClick={(e) => {
                          e.stopPropagation(); // 阻止触发卡片选择
                          runSingleTest(suite);
                        }}
                        disabled={!!currentRunningId || runningAll}
                        className={`text-xs font-semibold px-3 py-1.5 rounded-lg border transition shrink-0 ${
                          !!currentRunningId || runningAll
                            ? "bg-zinc-50 border-zinc-200 text-zinc-400 cursor-not-allowed"
                            : "bg-white hover:bg-zinc-100 border-zinc-200 hover:border-zinc-300 text-zinc-700 active:scale-95"
                        }`}
                      >
                        {status === "running" ? "运行中..." : "启动测试"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* 右侧：深邃终端控制台 */}
        <div className="lg:col-span-2">
          <div className="bg-zinc-950 border border-zinc-800 rounded-2xl shadow-xl overflow-hidden flex flex-col h-[650px] relative">
            
            {/* 终端头部 */}
            <div className="bg-zinc-900 px-5 py-3.5 border-b border-zinc-800 flex items-center justify-between select-none">
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-rose-500 opacity-80"></span>
                <span className="w-3 h-3 rounded-full bg-amber-500 opacity-80"></span>
                <span className="w-3 h-3 rounded-full bg-emerald-500 opacity-80"></span>
                <span className="text-xs font-semibold font-mono text-zinc-400 ml-2 tracking-wide">
                  SYSTEM DIAGNOSTICS CONSOLE {selectedSuiteId === "all" ? "— 全量自测" : `— 用例 ${selectedSuiteId}`}
                </span>
              </div>

              {/* 终端顶部操作按钮组 */}
              <div className="flex items-center gap-4 text-xs font-mono">
                {/* 自动滚屏开关 */}
                <label className="flex items-center gap-1.5 text-zinc-400 cursor-pointer hover:text-zinc-200 transition">
                  <input
                    type="checkbox"
                    checked={autoScroll}
                    onChange={(e) => setAutoScroll(e.target.checked)}
                    className="w-3 h-3 accent-indigo-500 cursor-pointer rounded"
                  />
                  自动滚动
                </label>

                <button
                  onClick={copyLogs}
                  disabled={activeLogs.length === 0}
                  className={`text-zinc-400 hover:text-zinc-200 transition ${
                    activeLogs.length === 0 ? "opacity-50 cursor-not-allowed" : ""
                  }`}
                >
                  📋 复制
                </button>

                <button
                  onClick={clearLogs}
                  disabled={Object.keys(consoleLogs).length === 0}
                  className={`text-zinc-400 hover:text-rose-400 transition ${
                    Object.keys(consoleLogs).length === 0 ? "opacity-50 cursor-not-allowed" : ""
                  }`}
                >
                  🗑️ 清空
                </button>
              </div>
            </div>

            {/* 终端 Tabs 切换条 */}
            <div className="bg-zinc-900/40 px-5 py-2 border-b border-zinc-800/80 flex items-center gap-2 overflow-x-auto select-none custom-scrollbar shrink-0">
              <button
                onClick={() => setSelectedSuiteId("all")}
                className={`px-3 py-1 rounded-md text-[10px] font-bold font-mono transition tracking-wider shrink-0 select-none ${
                  selectedSuiteId === "all"
                    ? "bg-indigo-600 text-white shadow-md shadow-indigo-500/20"
                    : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800"
                }`}
              >
                📦 全量自测 (ALL)
              </button>
              {testSuites.map((suite) => (
                <button
                  key={suite.id}
                  onClick={() => setSelectedSuiteId(suite.id)}
                  className={`px-2.5 py-1 rounded-md text-[10px] font-bold font-mono transition shrink-0 select-none ${
                    selectedSuiteId === suite.id
                      ? "bg-indigo-600 text-white shadow-md shadow-indigo-500/20"
                      : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800"
                  }`}
                >
                  ⚡ {suite.id} ({suite.name.replace("test_", "")})
                </button>
              ))}
            </div>

            {/* 终端主体日志内容 */}
            <div className="flex-1 p-6 overflow-y-auto font-mono text-xs select-text space-y-0.5 custom-scrollbar bg-zinc-950">
              {activeLogs.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-zinc-500 font-mono space-y-3 select-none">
                  <div className="text-4xl">⚡</div>
                  <div className="text-zinc-500">
                    {selectedSuiteId === "all"
                      ? "全量终端就绪。请在左侧启动单项测试，或一键全量健康自测。"
                      : `[用例 ${selectedSuiteId}] 终端就绪。目前无上一次的历史运行日志。`}
                  </div>
                  <div className="text-[10px] text-zinc-600 bg-zinc-900 border border-zinc-800 px-3 py-1 rounded-md">
                    admin@ranren-ai:~$ pytest {selectedSuiteId === "all" ? "-v" : testSuites.find(s => s.id === selectedSuiteId)?.file || ""}
                  </div>
                </div>
              ) : (
                <>
                  {activeLogs.map((line, index) => formatLogLine(line, index))}
                  
                  {/* 正在运行中的动态闪烁光标 */}
                  {currentRunningId && currentRunningId === selectedSuiteId && (
                    <div className="flex items-center gap-1 py-1 font-mono text-indigo-400 font-bold select-none">
                      <span>⚡ pytest is executing...</span>
                      <span className="w-1.5 h-4 bg-indigo-400 animate-pulse"></span>
                    </div>
                  )}
                  {runningAll && selectedSuiteId === "all" && (
                    <div className="flex items-center gap-1 py-1 font-mono text-indigo-400 font-bold select-none">
                      <span>⚡ pytest is executing full suite...</span>
                      <span className="w-1.5 h-4 bg-indigo-400 animate-pulse"></span>
                    </div>
                  )}
                </>
              )}
              <div ref={terminalEndRef} />
            </div>

            {/* 终端底部状态条 */}
            <div className="bg-zinc-900 border-t border-zinc-800 px-5 py-2 flex items-center justify-between text-[10px] text-zinc-500 font-mono select-none">
              <span>Lines: {activeLogs.length}</span>
              <span>Status: {currentRunningId === selectedSuiteId || (runningAll && selectedSuiteId === "all") ? "RUNNING" : "IDLE"}</span>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
}

// 1. 系统指标卡片组件
function MetricCard({
  title,
  value,
  description,
  color,
  icon,
}: {
  title: string;
  value: string;
  description: string;
  color: "green" | "indigo" | "blue" | "yellow" | "red";
  icon: string;
}) {
  const colorClasses = {
    green: "bg-emerald-50/50 border-emerald-200/60 text-emerald-700",
    indigo: "bg-indigo-50/50 border-indigo-200/60 text-indigo-700",
    blue: "bg-blue-50/50 border-blue-200/60 text-blue-700",
    yellow: "bg-amber-50/50 border-amber-200/60 text-amber-700",
    red: "bg-rose-50/50 border-rose-200/60 text-rose-700",
  };

  return (
    <div className={`rounded-xl border p-5 transition hover:shadow-sm ${colorClasses[color]}`}>
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium opacity-75">{title}</span>
        <span className="text-xl">{icon}</span>
      </div>
      <div className="mt-2.5 text-lg font-bold text-zinc-900">{value}</div>
      <p className="mt-1 text-xs text-zinc-600">{description}</p>
    </div>
  );
}

// 2. 测试状态 Bulb 指示灯
function StatusBulb({ status }: { status: TestStatus }) {
  const statusConfig = {
    idle: {
      color: "bg-zinc-300",
      glow: "",
      text: "Ready",
      textColor: "text-zinc-400",
    },
    running: {
      color: "bg-blue-500 animate-pulse",
      glow: "shadow-[0_0_8px_rgba(59,130,246,0.6)]",
      text: "Running",
      textColor: "text-blue-500",
    },
    passed: {
      color: "bg-emerald-500 animate-pulse",
      glow: "shadow-[0_0_8px_rgba(16,185,129,0.6)]",
      text: "Passed",
      textColor: "text-emerald-500",
    },
    failed: {
      color: "bg-rose-500 animate-pulse",
      glow: "shadow-[0_0_8px_rgba(244,63,94,0.6)]",
      text: "Failed",
      textColor: "text-rose-500",
    },
  };

  const conf = statusConfig[status];

  return (
    <div className="flex items-center gap-1.5 select-none bg-white border border-zinc-100 shadow-sm/50 px-2 py-0.5 rounded-full">
      <span className={`w-2 h-2 rounded-full ${conf.color} ${conf.glow}`}></span>
      <span className={`text-[10px] font-bold tracking-wider uppercase font-mono ${conf.textColor}`}>
        {conf.text}
      </span>
    </div>
  );
}
