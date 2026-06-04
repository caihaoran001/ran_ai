"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
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
  ShieldCheck,
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
  const formattedLastRun =
    typeof lastRunTime === "number" || (!Number.isNaN(Number(lastRunTime)) && lastRunTime !== "尚未运行")
      ? new Date(Number(lastRunTime) * 1000).toLocaleString("zh-CN", { hour12: false })
      : String(lastRunTime);

  return (
    <div className="space-y-6 pb-12">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link href="/dashboard" className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900">
            <ArrowLeft size={15} /> 返回数据看板
          </Link>
          <h1 className="mt-3 text-2xl font-bold text-zinc-950">系统诊断控制台</h1>
          <p className="mt-1 text-sm text-zinc-500">健康检查验证稳定性，真实旅程验证内容质量；两类测试均不污染客户数据。</p>
        </div>
        <button
          onClick={runHealthChecks}
          disabled={busy}
          className="inline-flex h-10 items-center gap-2 rounded-md bg-zinc-950 px-4 text-sm font-semibold text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:bg-zinc-400"
        >
          {runningAll ? <LoaderCircle size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
          {runningAll ? "正在检查" : "运行安全健康检查"}
        </button>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          icon={<Database size={18} />}
          label="数据库"
          value={summary.database.status === "connected" ? "连接正常" : summaryLoading ? "检测中" : "连接异常"}
          detail={summary.database.message}
          tone={summary.database.status === "connected" ? "green" : "red"}
        />
        <SummaryCard
          icon={<BrainCircuit size={18} />}
          label="AI 连通"
          value={
            typeof summary.ai_probe.latency_ms === "number"
              ? `${(summary.ai_probe.latency_ms / 1000).toFixed(2)} 秒`
              : summaryLoading
                ? "检测中"
                : "不可用"
          }
          detail={summary.ai_probe.model || summary.ai_probe.message}
          tone={summary.ai_probe.status === "connected" ? "blue" : "red"}
        />
        <SummaryCard
          icon={<Coins size={18} />}
          label="最近真实 E2E"
          value={`¥${(latestDiagnostic?.estimated_cost_yuan || 0).toFixed(2)}`}
          detail={`${latestDiagnostic?.calls || 0} 次调用 · ${(latestDiagnostic?.tokens || 0).toLocaleString()} tokens`}
          tone="violet"
        />
        <SummaryCard
          icon={<Clock3 size={18} />}
          label="最近运行"
          value={formattedLastRun}
          detail={`${healthSuites.length} 个健康套件 · ${realE2ESuite ? "1 个真实旅程套件" : "未配置真实旅程"}`}
          tone="amber"
        />
      </section>

      <section className="rounded-lg border border-zinc-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-100 px-5 py-4">
          <div>
            <h2 className="font-semibold text-zinc-950">安全健康检查</h2>
            <p className="mt-0.5 text-xs text-zinc-500">Mock AI 与 Mock 通道，重点检查接口、状态流转、数据契约和回归稳定性。</p>
          </div>
          <span className="rounded bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700">业务数据回滚</span>
        </div>
        <div className="grid gap-px bg-zinc-100 md:grid-cols-2 xl:grid-cols-4">
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
        <section className="grid gap-5 rounded-lg border border-indigo-200 bg-indigo-50/40 p-5 xl:grid-cols-[1fr_auto]">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-indigo-950">
              <FlaskConical size={17} /> 真实模型旅程验证
              <StatusBadge status={statuses[realE2ESuite.id] || "idle"} />
            </div>
            <h2 className="mt-3 text-xl font-bold text-zinc-950">10 类用户，从入营问卷走到第 28 天</h2>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-zinc-600">
              覆盖积极配合、焦虑、沉默不打卡、轮班、咖啡因敏感、平台期质疑、退款、慢病用药、续营和低落恢复。
              每类用户都会验证画像与文档一致性、聊天回复、风控、提醒、成效提取，并由模型评审内容是否自然合理。
            </p>
            <div className="mt-4 flex flex-wrap gap-2 text-xs">
              <InfoPill icon={<Users size={13} />} text="10 类用户画像" />
              <InfoPill icon={<Clock3 size={13} />} text="逐日推进 28 天" />
              <InfoPill icon={<ShieldCheck size={13} />} text="业务数据全部回滚" />
              <InfoPill icon={<Coins size={13} />} text="诊断费用单独保留" />
            </div>
          </div>
          <button
            onClick={() => void runSuite(realE2ESuite)}
            disabled={busy}
            className="inline-flex h-10 items-center justify-center gap-2 self-center rounded-md bg-indigo-600 px-4 text-sm font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-zinc-400"
          >
            {currentRunningId === realE2ESuite.id ? <LoaderCircle size={16} className="animate-spin" /> : <Play size={16} />}
            {currentRunningId === realE2ESuite.id ? "真实旅程运行中" : "运行真实旅程验证"}
          </button>
        </section>
      )}

      <section className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200 px-4 py-3">
          <div className="flex flex-wrap gap-1">
            <LogTab active={selectedSuiteId === "all"} label="健康检查汇总" onClick={() => setSelectedSuiteId("all")} />
            {testSuites.map((suite) => (
              <LogTab
                key={suite.id}
                active={selectedSuiteId === suite.id}
                label={suite.display_id}
                onClick={() => setSelectedSuiteId(suite.id)}
              />
            ))}
          </div>
          <div className="flex items-center gap-1">
            <label className="mr-2 flex items-center gap-1.5 text-xs text-zinc-500">
              <input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} />
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
        <div className="h-[440px] overflow-y-auto bg-zinc-950 px-5 py-4 font-mono text-xs leading-6 text-zinc-300">
          {activeLogs.length === 0 ? (
            <div className="flex h-full items-center justify-center text-zinc-600">选择测试套件并开始运行，日志会在这里实时出现。</div>
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
  return (
    <div onClick={onSelect} className={`min-h-40 bg-white p-4 transition ${selected ? "ring-2 ring-inset ring-zinc-900" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <span className="font-mono text-xs font-bold text-zinc-400">{suite.display_id}</span>
        <StatusBadge status={status} />
      </div>
      <h3 className="mt-3 text-sm font-semibold text-zinc-950">{suite.name.replace(/^test_\d+_/, "")}</h3>
      <p className="mt-1.5 line-clamp-3 text-xs leading-5 text-zinc-500">{suite.description}</p>
      <div className="mt-4 flex items-center justify-between">
        <span className="text-xs text-zinc-400">{duration || "尚未运行"}</span>
        <button
          onClick={(event) => {
            event.stopPropagation();
            onRun();
          }}
          disabled={disabled}
          title={`运行 ${suite.display_id}`}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-zinc-200 text-zinc-600 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:text-zinc-300"
        >
          {status === "running" ? <LoaderCircle size={14} className="animate-spin" /> : <Play size={14} />}
        </button>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: TestStatus }) {
  const styles = {
    idle: "bg-zinc-100 text-zinc-500",
    running: "bg-blue-50 text-blue-700",
    passed: "bg-emerald-50 text-emerald-700",
    failed: "bg-rose-50 text-rose-700",
  };
  const labels = { idle: "待运行", running: "运行中", passed: "通过", failed: "失败" };
  return <span className={`rounded px-2 py-0.5 text-[11px] font-medium ${styles[status]}`}>{labels[status]}</span>;
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
    green: "bg-emerald-50 text-emerald-700",
    blue: "bg-sky-50 text-sky-700",
    violet: "bg-violet-50 text-violet-700",
    amber: "bg-amber-50 text-amber-700",
    red: "bg-rose-50 text-rose-700",
  };
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="flex items-center gap-2 text-xs font-medium text-zinc-500">
        <span className={`rounded-md p-1.5 ${tones[tone]}`}>{icon}</span>
        {label}
      </div>
      <div className="mt-3 truncate text-lg font-bold text-zinc-950" title={value}>{value}</div>
      <p className="mt-1 truncate text-xs text-zinc-500" title={detail}>{detail}</p>
    </div>
  );
}

function InfoPill({ icon, text }: { icon: React.ReactNode; text: string }) {
  return <span className="inline-flex items-center gap-1.5 rounded bg-white px-2.5 py-1 text-indigo-700 ring-1 ring-indigo-100">{icon}{text}</span>;
}

function LogTab({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`rounded px-2.5 py-1 text-xs font-medium ${active ? "bg-zinc-950 text-white" : "text-zinc-500 hover:bg-zinc-100"}`}>
      {label}
    </button>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button title={label} onClick={onClick} className="inline-flex h-8 w-8 items-center justify-center rounded-md text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900">
      {children}
    </button>
  );
}

function LogLine({ line }: { line: string }) {
  const failed = /FAILED|ERROR|测试失败|运行异常/.test(line);
  const passed = /PASSED|测试成功|SUCCESS/.test(line);
  return (
    <div className={`break-all ${failed ? "text-rose-400" : passed ? "text-emerald-400" : ""}`}>
      {failed ? <XCircle size={12} className="mr-1 inline" /> : passed ? <CheckCircle2 size={12} className="mr-1 inline" /> : null}
      {line}
    </div>
  );
}
