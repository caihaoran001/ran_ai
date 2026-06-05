"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowLeft,
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  Coins,
  Copy,
  Database,
  FlaskConical,
  LoaderCircle,
  Play,
  RotateCcw,
  Server,
  ShieldCheck,
  Terminal,
  Trash2,
  Users,
  XCircle,
} from "lucide-react";
import type { DiagnosticsSummary } from "@/lib/api";

type TestSuite = {
  id: string;
  display_id: string;
  name: string;
  description: string;
  file: string;
  tier: "health" | "real_e2e";
  included_in_health_check: boolean;
  data_policy: "rollback" | "persistent";
  requires_confirmation: boolean;
};

type TestStatus = "idle" | "running" | "passed" | "failed";

const EMPTY_SUMMARY: DiagnosticsSummary = {
  database: { status: "checking", message: "数据库探针检测中" },
  ai_probe: { status: "checking", message: "AI 探针检测中", latency_ms: null, model: null },
  checked_at: 0,
};

export default function SystemDiagnosticsPage() {
  const [testSuites, setTestSuites] = useState<TestSuite[]>([]);
  const [statuses, setStatuses] = useState<Record<string, TestStatus>>({});
  const [durations, setDurations] = useState<Record<string, string>>({});
  const [consoleLogs, setConsoleLogs] = useState<Record<string, string[]>>({});
  const [selectedSuiteId, setSelectedSuiteId] = useState("all");
  const [runningAll, setRunningAll] = useState(false);
  const [currentRunningId, setCurrentRunningId] = useState<string | null>(null);
  const [lastRunTime, setLastRunTime] = useState<string | number>("尚未运行");
  const [summary, setSummary] = useState<DiagnosticsSummary>(EMPTY_SUMMARY);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  const healthSuites = testSuites.filter((suite) => suite.included_in_health_check);
  const realE2ESuite = testSuites.find((suite) => suite.tier === "real_e2e");
  const activeLogs = consoleLogs[selectedSuiteId] || [];
  const busy = runningAll || currentRunningId !== null;

  useEffect(() => {
    void loadInitialData();
    void loadSummary();
  }, []);

  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => void syncHistory(), 2000);
    return () => window.clearInterval(timer);
  }, [busy]);

  useEffect(() => {
    if (autoScroll) terminalEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeLogs, autoScroll]);

  async function loadInitialData() {
    try {
      const suitesResponse = await fetch("/api/backend/diagnostics/test-suites");
      if (!suitesResponse.ok) throw new Error(await suitesResponse.text());
      const suites: TestSuite[] = await suitesResponse.json();
      setTestSuites(suites);
      const historyResponse = await fetch("/api/backend/diagnostics/history");
      if (!historyResponse.ok) return;
      applyHistory(await historyResponse.json(), suites);
    } catch (error) {
      console.error("加载诊断信息失败", error);
    }
  }

  async function syncHistory() {
    const response = await fetch("/api/backend/diagnostics/history", { cache: "no-store" });
    if (!response.ok) return;
    applyHistory(await response.json(), testSuites);
  }

  function applyHistory(history: Record<string, unknown>, suites: TestSuite[]) {
    const historyStatuses = (history.statuses || {}) as Record<string, TestStatus>;
    setStatuses(Object.fromEntries(suites.map((suite) => [suite.id, historyStatuses[suite.id] || "idle"])));
    setDurations((history.durations || {}) as Record<string, string>);
    const logs = history.console_logs;
    setConsoleLogs(Array.isArray(logs) ? { all: logs as string[] } : ((logs || {}) as Record<string, string[]>));
    setLastRunTime((history.last_run_time as string | number) || "尚未运行");
    const activeRun = history.active_run as { mode?: string; suite_id?: string } | null;
    setRunningAll(activeRun?.mode === "health");
    setCurrentRunningId(activeRun?.mode && activeRun.mode !== "health" ? activeRun.suite_id || null : null);
  }

  async function loadSummary() {
    setSummaryLoading(true);
    try {
      const response = await fetch("/api/backend/diagnostics/summary", { cache: "no-store" });
      if (!response.ok) throw new Error(await response.text());
      setSummary(await response.json());
    } catch (error) {
      const message = error instanceof Error ? error.message : "诊断摘要加载失败";
      setSummary({
        database: { status: "failed", message },
        ai_probe: { status: "failed", message, latency_ms: null, model: null },
        checked_at: 0,
      });
    } finally {
      setSummaryLoading(false);
    }
  }

  async function runSuite(suite: TestSuite) {
    if (busy) return;
    if (
      suite.requires_confirmation &&
      !window.confirm("将运行真实模型与 10 类用户的 28 天旅程。业务测试数据会回滚，只保留诊断费用和运行记录。确认开始吗？")
    ) {
      return;
    }
    setSelectedSuiteId(suite.id);
    setCurrentRunningId(suite.id);
    setStatuses((current) => ({ ...current, [suite.id]: "running" }));
    setConsoleLogs((current) => ({ ...current, [suite.id]: [`开始运行 ${suite.display_id} ${suite.name}`] }));
    await consumeRun(`/api/backend/diagnostics/run/${suite.id}${suite.requires_confirmation ? "?confirm_real_e2e=true" : ""}`, suite.id);
    setCurrentRunningId(null);
  }

  async function runHealthChecks() {
    if (busy) return;
    setRunningAll(true);
    setSelectedSuiteId("all");
    setConsoleLogs((current) => ({ ...current, all: ["开始运行安全健康自测 01-08"] }));
    setStatuses((current) => ({
      ...current,
      ...Object.fromEntries(healthSuites.map((suite) => [suite.id, "running" as TestStatus])),
    }));
    await consumeRun("/api/backend/diagnostics/run/all", "all");
    setRunningAll(false);
  }

  async function consumeRun(url: string, logKey: string) {
    try {
      const response = await fetch(url);
      if (!response.ok || !response.body) throw new Error(await response.text());
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const lines = decoder.decode(value, { stream: true }).split("\n").filter(Boolean);
        setConsoleLogs((current) => ({ ...current, [logKey]: [...(current[logKey] || []), ...lines] }));
      }
      await syncHistory();
      await loadSummary();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setConsoleLogs((current) => ({ ...current, [logKey]: [...(current[logKey] || []), `运行异常: ${message}`] }));
    }
  }

  async function clearHistory() {
    await fetch("/api/backend/diagnostics/clear-history", { method: "POST" });
    setConsoleLogs({});
    setStatuses(Object.fromEntries(testSuites.map((suite) => [suite.id, "idle"])));
    setDurations({});
    setLastRunTime("尚未运行");
    setSelectedSuiteId("all");
  }

  const latestDiagnostic = summary.diagnostic_costs?.latest;
  const passedHealthCount = healthSuites.filter((suite) => statuses[suite.id] === "passed").length;
  const failedHealthCount = healthSuites.filter((suite) => statuses[suite.id] === "failed").length;
  const runningHealthCount = healthSuites.filter((suite) => statuses[suite.id] === "running").length;
  const healthProgress = healthSuites.length ? Math.round((passedHealthCount / healthSuites.length) * 100) : 0;
  const selectedSuite = selectedSuiteId === "all" ? null : testSuites.find((suite) => suite.id === selectedSuiteId);
  const consoleTitle = selectedSuite
    ? `${selectedSuite.display_id} ${selectedSuite.name}`
    : "安全健康检查汇总";
  const formattedLastRun =
    typeof lastRunTime === "number" || (!Number.isNaN(Number(lastRunTime)) && lastRunTime !== "尚未运行")
      ? new Date(Number(lastRunTime) * 1000).toLocaleString("zh-CN", { hour12: false })
      : String(lastRunTime);

  return (
    <div className="min-h-[calc(100vh-5rem)] pb-12 text-zinc-950">
      <header className="rounded-lg border border-cyan-100 bg-gradient-to-br from-cyan-50 via-white to-emerald-50 px-5 py-5 shadow-sm">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-4xl">
            <Link
              href="/dashboard"
              className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-sm font-medium text-cyan-800 transition-colors hover:bg-white/70 hover:text-cyan-950 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-600"
            >
              <ArrowLeft size={15} /> 返回数据看板
            </Link>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-bold text-zinc-950">系统诊断控制台</h1>
              <RunStateBadge busy={busy} failedCount={failedHealthCount} />
            </div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600">
              以安全健康检查守住系统稳定性，以真实模型旅程验证内容质量。测试业务数据回滚，真实模型费用独立记录。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => void loadSummary()}
              disabled={summaryLoading}
              className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-md border border-cyan-200 bg-white px-3 text-sm font-semibold text-cyan-800 shadow-sm transition-colors hover:border-cyan-300 hover:bg-cyan-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-600 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {summaryLoading ? <LoaderCircle size={16} className="animate-spin" /> : <RotateCcw size={16} />}
              刷新状态
            </button>
            <button
              onClick={runHealthChecks}
              disabled={busy}
              className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-md bg-cyan-800 px-4 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-cyan-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-600 disabled:cursor-not-allowed disabled:bg-zinc-400"
            >
              {runningAll ? <LoaderCircle size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
              {runningAll ? "安全检查运行中" : "运行安全健康检查"}
            </button>
          </div>
        </div>
      </header>

      <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          icon={<Database size={18} />}
          label="数据库状态"
          value={summary.database.status === "connected" ? "CONNECTED" : summaryLoading ? "CHECKING" : "FAILED"}
          detail={summary.database.message}
          tone={summary.database.status === "connected" ? "green" : "red"}
        />
        <SummaryCard
          icon={<BrainCircuit size={18} />}
          label="AI 接口延迟"
          value={
            typeof summary.ai_probe.latency_ms === "number"
              ? `${(summary.ai_probe.latency_ms / 1000).toFixed(2)}s`
              : summaryLoading
                ? "CHECKING"
                : "FAILED"
          }
          detail={summary.ai_probe.model || summary.ai_probe.message}
          tone={summary.ai_probe.status === "connected" ? "blue" : "red"}
        />
        <SummaryCard
          icon={<Coins size={18} />}
          label="最近真实 E2E 成本"
          value={`¥${(latestDiagnostic?.estimated_cost_yuan || 0).toFixed(2)}`}
          detail={`${latestDiagnostic?.calls || 0} 次调用 · ${(latestDiagnostic?.tokens || 0).toLocaleString()} tokens`}
          tone="violet"
        />
        <SummaryCard
          icon={<Clock3 size={18} />}
          label="最近运行时间"
          value={formattedLastRun}
          detail={`${healthSuites.length} 个健康套件 · ${realE2ESuite ? "E2E-01 已配置" : "未配置真实旅程"}`}
          tone="amber"
        />
      </section>

      <section className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-5">
          <section className="overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-sm">
            <div className="border-b border-zinc-100 px-5 py-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <Server size={18} className="text-cyan-700" />
                    <h2 className="font-semibold text-zinc-950">安全健康检查</h2>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-zinc-500">
                    Mock AI 与 Mock 通道，覆盖接口、状态流转、数据契约、QiWe 通道和成效引擎回归。
                  </p>
                </div>
                <div className="min-w-56">
                  <div className="mb-1 flex items-center justify-between text-xs text-zinc-500">
                    <span>通过进度</span>
                    <span>{passedHealthCount}/{healthSuites.length}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-zinc-100">
                    <div className="h-full rounded-full bg-cyan-700 transition-all" style={{ width: `${healthProgress}%` }} />
                  </div>
                </div>
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-3">
                <MiniMetric label="通过" value={String(passedHealthCount)} tone="green" />
                <MiniMetric label="运行中" value={String(runningHealthCount)} tone="blue" />
                <MiniMetric label="失败" value={String(failedHealthCount)} tone="red" />
              </div>
            </div>
            <div className="grid gap-3 bg-zinc-50 p-3 md:grid-cols-2 2xl:grid-cols-4">
              {healthSuites.map((suite) => (
                <SuiteCard
                  key={suite.id}
                  suite={suite}
                  status={statuses[suite.id] || "idle"}
                  duration={durations[suite.id]}
                  selected={selectedSuiteId === suite.id}
                  disabled={busy}
                  onSelect={() => setSelectedSuiteId(suite.id)}
                  onRun={() => void runSuite(suite)}
                />
              ))}
            </div>
          </section>

          {realE2ESuite && (
            <section className="overflow-hidden rounded-lg border border-cyan-200 bg-white shadow-sm">
              <div className="grid gap-0 xl:grid-cols-[1fr_260px]">
                <div className="p-5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-cyan-50 text-cyan-700 ring-1 ring-cyan-100">
                      <FlaskConical size={17} />
                    </span>
                    <div className="font-semibold text-zinc-950">真实模型旅程验证</div>
                    <StatusBadge status={statuses[realE2ESuite.id] || "idle"} />
                  </div>
                  <h2 className="mt-4 text-xl font-bold text-zinc-950">10 类用户，从入营问卷走到第 28 天</h2>
                  <p className="mt-2 max-w-4xl text-sm leading-6 text-zinc-600">
                    覆盖积极配合、焦虑、沉默不打卡、轮班、咖啡因敏感、平台期质疑、退款、慢病用药、续营和低落恢复。
                    每类用户都会验证画像与文档一致性、聊天回复、风控、提醒、成效提取，并由模型自动评审内容质量。
                  </p>
                  <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                    <InfoPill icon={<Users size={14} />} text="10 类用户画像" />
                    <InfoPill icon={<Clock3 size={14} />} text="逐日推进 28 天" />
                    <InfoPill icon={<ShieldCheck size={14} />} text="业务数据回滚" />
                    <InfoPill icon={<Coins size={14} />} text="成本独立记录" />
                  </div>
                </div>
                <div className="border-t border-cyan-100 bg-cyan-50/70 p-5 xl:border-l xl:border-t-0">
                  <div className="text-sm font-semibold text-cyan-950">E2E-01</div>
                  <p className="mt-2 text-xs leading-5 text-cyan-800">
                    会调用真实模型，完整运行预计 30-60 分钟。运行时会显示心跳日志，请勿重复点击。
                  </p>
                  <button
                    onClick={() => void runSuite(realE2ESuite)}
                    disabled={busy}
                    className="mt-4 inline-flex h-10 w-full cursor-pointer items-center justify-center gap-2 rounded-md bg-emerald-700 px-4 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-emerald-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600 disabled:cursor-not-allowed disabled:bg-zinc-400"
                  >
                    {currentRunningId === realE2ESuite.id ? <LoaderCircle size={16} className="animate-spin" /> : <Play size={16} />}
                    {currentRunningId === realE2ESuite.id ? "真实旅程运行中" : "运行真实旅程验证"}
                  </button>
                </div>
              </div>
            </section>
          )}
        </div>

        <aside className="space-y-5">
          <section className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm">
            <div className="flex items-center gap-2 text-sm font-semibold text-zinc-950">
              <Activity size={17} className="text-cyan-700" />
              运行摘要
            </div>
            <div className="mt-4 space-y-3">
              <SideRow label="当前状态" value={busy ? "运行中" : failedHealthCount ? "存在失败" : "空闲"} />
              <SideRow label="选中日志" value={selectedSuite ? selectedSuite.display_id : "汇总"} />
              <SideRow label="真实 E2E 成本" value={`¥${(latestDiagnostic?.estimated_cost_yuan || 0).toFixed(2)}`} />
              <SideRow label="最近运行" value={formattedLastRun} />
            </div>
          </section>
          <section className="rounded-lg border border-amber-200 bg-amber-50 p-4">
            <div className="flex items-start gap-2">
              <AlertTriangle size={17} className="mt-0.5 text-amber-700" />
              <div>
                <div className="text-sm font-semibold text-amber-950">真实模型提示</div>
                <p className="mt-1 text-xs leading-5 text-amber-900">
                  E2E-01 会产生真实模型费用，但业务数据会回滚。安全健康检查不会调用真实模型。
                </p>
              </div>
            </div>
          </section>
        </aside>
      </section>

      <section className="mt-5 overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-sm">
        <div className="flex flex-col gap-3 border-b border-zinc-200 px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-zinc-100 text-zinc-700">
              <Terminal size={16} />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-zinc-950">诊断终端</h2>
              <p className="text-xs text-zinc-500">{consoleTitle}</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-md border border-zinc-200 px-2.5 text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-50">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(event) => setAutoScroll(event.target.checked)}
                className="h-3.5 w-3.5 rounded border-zinc-300 text-cyan-700 focus:ring-cyan-600"
              />
              自动滚动
            </label>
            <IconButton label="复制日志" onClick={() => void navigator.clipboard.writeText(activeLogs.join("\n"))}>
              <Copy size={15} />
            </IconButton>
            <IconButton label="刷新摘要" onClick={() => void loadSummary()}>
              <RotateCcw size={15} />
            </IconButton>
            <IconButton label="清空历史" onClick={() => void clearHistory()}>
              <Trash2 size={15} />
            </IconButton>
          </div>
        </div>
        <div className="border-b border-zinc-200 bg-zinc-50 px-3 py-2">
          <div className="flex gap-1 overflow-x-auto">
            <LogTab active={selectedSuiteId === "all"} label="汇总" onClick={() => setSelectedSuiteId("all")} />
            {testSuites.map((suite) => (
              <LogTab
                key={suite.id}
                active={selectedSuiteId === suite.id}
                label={suite.display_id}
                onClick={() => setSelectedSuiteId(suite.id)}
              />
            ))}
          </div>
        </div>
        <div className="h-[520px] overflow-y-auto bg-[#071417] px-5 py-4 font-mono text-xs leading-6 text-cyan-50">
          {activeLogs.length === 0 ? (
            <div className="flex h-full items-center justify-center text-cyan-200/50">选择测试套件并开始运行，日志会在这里实时出现。</div>
          ) : (
            activeLogs.map((line, index) => <LogLine key={`${index}-${line}`} line={line} />)
          )}
          <div ref={terminalEndRef} />
        </div>
      </section>
    </div>
  );
}

function SuiteCard({
  suite,
  status,
  duration,
  selected,
  disabled,
  onSelect,
  onRun,
}: {
  suite: TestSuite;
  status: TestStatus;
  duration?: string;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  onRun: () => void;
}) {
  const shortName = suite.name
    .replace(/^test_\d+_/, "")
    .replace(/^test_e2e_\d+_/, "");
  return (
    <article
      onClick={onSelect}
      className={`group flex min-h-44 cursor-pointer flex-col rounded-md border bg-white p-4 shadow-sm transition-colors hover:border-cyan-300 hover:bg-cyan-50/40 focus-within:border-cyan-500 ${
        selected ? "border-cyan-500 ring-2 ring-cyan-100" : "border-zinc-200"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="inline-flex h-7 min-w-7 items-center justify-center rounded-md bg-zinc-100 px-2 font-mono text-xs font-bold text-zinc-600 group-hover:bg-white">
          {suite.display_id}
        </span>
        <StatusBadge status={status} />
      </div>
      <h3 className="mt-3 text-sm font-semibold text-zinc-950">{shortName}</h3>
      <p className="mt-1.5 line-clamp-3 text-xs leading-5 text-zinc-500">{suite.description}</p>
      <div className="mt-auto flex items-center justify-between pt-4">
        <span className="text-xs font-medium text-zinc-400">{duration || "尚未运行"}</span>
        <button
          onClick={(event) => {
            event.stopPropagation();
            onRun();
          }}
          disabled={disabled}
          title={`运行 ${suite.display_id}`}
          className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-md border border-zinc-200 bg-white text-zinc-700 shadow-sm transition-colors hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-600 disabled:cursor-not-allowed disabled:text-zinc-300 disabled:opacity-60"
        >
          {status === "running" ? <LoaderCircle size={14} className="animate-spin" /> : <Play size={14} />}
        </button>
      </div>
    </article>
  );
}

function StatusBadge({ status }: { status: TestStatus }) {
  const styles = {
    idle: "bg-zinc-100 text-zinc-600 ring-zinc-200",
    running: "bg-sky-50 text-sky-800 ring-sky-200",
    passed: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    failed: "bg-rose-50 text-rose-800 ring-rose-200",
  };
  const labels = { idle: "待运行", running: "运行中", passed: "通过", failed: "失败" };
  return <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ${styles[status]}`}>{labels[status]}</span>;
}

function SummaryCard({
  icon,
  label,
  value,
  detail,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: "green" | "blue" | "violet" | "amber" | "red";
}) {
  const tones = {
    green: "bg-emerald-50 text-emerald-700 ring-emerald-100",
    blue: "bg-cyan-50 text-cyan-700 ring-cyan-100",
    violet: "bg-indigo-50 text-indigo-700 ring-indigo-100",
    amber: "bg-amber-50 text-amber-700 ring-amber-100",
    red: "bg-rose-50 text-rose-700 ring-rose-100",
  };
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs font-semibold text-zinc-500">{label}</div>
        <span className={`inline-flex h-8 w-8 items-center justify-center rounded-md ring-1 ${tones[tone]}`}>{icon}</span>
      </div>
      <div className="mt-3 truncate text-xl font-bold text-zinc-950" title={value}>{value}</div>
      <p className="mt-1 truncate text-xs leading-5 text-zinc-500" title={detail}>{detail}</p>
    </div>
  );
}

function InfoPill({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-white px-2.5 py-2 text-xs font-medium text-cyan-800 ring-1 ring-cyan-100">
      {icon}
      {text}
    </span>
  );
}

function LogTab({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`h-8 cursor-pointer rounded-md px-3 text-xs font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-600 ${
        active ? "bg-cyan-800 text-white" : "text-zinc-600 hover:bg-white hover:text-zinc-950"
      }`}
    >
      {label}
    </button>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      title={label}
      onClick={onClick}
      className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md border border-zinc-200 bg-white text-zinc-500 transition-colors hover:border-cyan-200 hover:bg-cyan-50 hover:text-cyan-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-600"
    >
      {children}
    </button>
  );
}

function LogLine({ line }: { line: string }) {
  const failed = /FAILED|ERROR|测试失败|运行异常/.test(line);
  const passed = /PASSED|测试成功|SUCCESS/.test(line);
  const running = /当前执行套件|执行命令|仍在运行|启动/.test(line);
  return (
    <div className={`break-all ${failed ? "text-rose-300" : passed ? "text-emerald-300" : running ? "text-cyan-200" : "text-cyan-50/80"}`}>
      {failed ? <XCircle size={12} className="mr-1 inline" /> : passed ? <CheckCircle2 size={12} className="mr-1 inline" /> : null}
      {line}
    </div>
  );
}

function MiniMetric({ label, value, tone }: { label: string; value: string; tone: "green" | "blue" | "red" }) {
  const tones = {
    green: "bg-emerald-50 text-emerald-800 ring-emerald-100",
    blue: "bg-cyan-50 text-cyan-800 ring-cyan-100",
    red: "bg-rose-50 text-rose-800 ring-rose-100",
  };
  return (
    <div className={`rounded-md px-3 py-2 ring-1 ${tones[tone]}`}>
      <div className="text-xs font-medium opacity-80">{label}</div>
      <div className="mt-1 text-lg font-bold">{value}</div>
    </div>
  );
}

function SideRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-zinc-100 pb-3 last:border-0 last:pb-0">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="max-w-44 text-right text-sm font-semibold text-zinc-900">{value}</span>
    </div>
  );
}

function RunStateBadge({ busy, failedCount }: { busy: boolean; failedCount: number }) {
  if (busy) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-800 ring-1 ring-sky-200">
        <LoaderCircle size={13} className="animate-spin" />
        运行中
      </span>
    );
  }
  if (failedCount > 0) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-3 py-1 text-xs font-semibold text-rose-800 ring-1 ring-rose-200">
        <XCircle size={13} />
        有失败项
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800 ring-1 ring-emerald-200">
      <CheckCircle2 size={13} />
      待命
    </span>
  );
}
