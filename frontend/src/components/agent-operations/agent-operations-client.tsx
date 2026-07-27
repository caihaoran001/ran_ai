"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import {
  Activity,
  Archive,
  ArrowRight,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  Coins,
  MessageSquareReply,
  TriangleAlert,
  Wrench,
  Search,
  Filter,
  RefreshCw,
  ExternalLink,
  ChevronRight,
  MousePointerClick,
  Info,
} from "lucide-react";
import { formatChinaShortDateTime } from "@/lib/format";
import { RunDetailDrawer, RunDetailData } from "./run-detail-drawer";
import { ToolDetailModal, ToolStat } from "./tool-detail-modal";
import { ScenarioCostModal, CaseCostDrawer, ScenarioCost, CaseCost } from "./cost-detail-modal";
import { OutcomeDetailModal, OutcomeDetail } from "./outcome-detail-modal";

type OutcomeCount = { key: string; count: number };
type RuntimeSummary = {
  runs: number;
  completed_runs: number;
  failed_runs: number;
  running_runs: number;
  model_calls: number;
  tool_calls: number;
  successful_tool_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_yuan: number;
  input_count: number;
  output_count: number;
  dropped_count: number;
  latency_p50_ms?: number | null;
  latency_p95_ms?: number | null;
  outcomes: OutcomeCount[];
};
type ArchivistSummary = RuntimeSummary & {
  output_breakdown: Record<string, number>;
  draft_statuses: Record<string, number>;
};

type RecentRun = RunDetailData;
type ArchivistBatch = RunDetailData;

export type AgentOperationsData = {
  days: number;
  period_start: string;
  generated_at: string;
  all_agents: RuntimeSummary;
  reply: RuntimeSummary;
  reminders: RuntimeSummary;
  archivist: ArchivistSummary;
  tools: ToolStat[];
  costs: {
    calls: number;
    successful_calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    estimated_cost_yuan: number;
    service_case_count: number;
    unassigned_cost_yuan: number;
    scenarios: ScenarioCost[];
    cases: CaseCost[];
  };
  recent_runs: RecentRun[];
  archivist_batches: ArchivistBatch[];
};

const TABS = [
  { key: "overview", label: "总览", icon: Activity },
  { key: "reply", label: "回复 Agent", icon: Bot },
  { key: "archivist", label: "档案官", icon: Archive },
  { key: "costs", label: "成本明细", icon: Coins },
] as const;
type TabKey = (typeof TABS)[number]["key"];

const OUTCOME_LABELS: Record<string, string> = {
  reply_sent: "已发送回复",
  intentional_no_reply: "Agent 主动不回复",
  human_handoff: "已转人工",
  handoff_throttled: "转人工提醒处于节流期",
  handoff_already_recorded: "转人工消息已存在",
  staff_preempted: "班主任抢先回复",
  stale_reply_suppressed: "被更新的用户消息覆盖",
  newer_message_queued: "队列中已有更新消息",
  ai_disabled: "全局 AI 回复关闭",
  ai_disabled_before_send: "发送前全局开关关闭",
  case_auto_reply_disabled: "当前服务单自动回复关闭",
  case_disabled_before_send: "发送前服务单开关关闭",
  inactive_case: "服务单已不在服务中",
  no_active_case: "无有效服务单",
  agent_generation_failed: "回复生成失败",
  send_failed: "发送失败",
  send_unknown: "发送结果未知",
  send_policy_suppressed: "发送策略拦截",
  reply_already_recorded: "回复已记录",
  non_chat_message: "非聊天消息",
  workflow_completed: "流程完成",
  archived_with_output: "归档成功且有产出",
  archived_no_output: "归档成功但无产出",
  archivist_failed: "归档失败",
  archivist_replay_failed: "人工重放失败",
  reminder_sent: "提醒已发送",
  reminder_generation_failed: "提醒生成失败",
  reminder_empty: "提醒生成内容为空",
  reminder_send_failed: "提醒发送失败",
  running: "运行中",
};

const OUTPUT_LABELS: Record<string, string> = {
  checkin_days: "打卡日期",
  outcomes: "正向成效",
  profile_facts: "稳定画像事实",
  state_facts: "当前状态事实",
  personal_guidance: "个人指导",
  corrections: "纠正记录",
  faq_drafts: "FAQ 草稿",
  memory_updates: "用户小结 / 案例素材更新",
  user_summary_updates: "用户历程小结",
  case_material_updates: "优秀案例素材小结",
  case_drafts: "优秀案例草稿",
};

const TOOL_LABELS: Record<string, string> = {
  get_service_progress: "查看服务进度",
  get_recent_checkins: "查看近期打卡",
  get_weight_trend: "查看体重趋势",
  list_delivery_documents: "列出交付文件",
  read_delivery_document: "读取交付文件",
  list_group_files: "列出群文件",
  read_group_file: "读取群文件",
  search_knowledge_base: "检索知识库",
  create_group_reminder: "创建群提醒",
  list_group_reminders: "查看群提醒",
  cancel_group_reminder: "取消群提醒",
  web_search_lifestyle: "生活方式联网检索",
};

export function AgentOperationsClient({
  data,
  initialTab,
  initialDays,
}: {
  data: AgentOperationsData;
  initialTab: TabKey;
  initialDays: number;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [isPending, startTransition] = useTransition();

  // Active filters & search
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [agentFilter, setAgentFilter] = useState<string>("all");

  // Selected Items for Drill-down Drawers/Modals
  const [selectedRun, setSelectedRun] = useState<RunDetailData | null>(null);
  const [selectedTool, setSelectedTool] = useState<ToolStat | null>(null);
  const [selectedScenario, setSelectedScenario] = useState<ScenarioCost | null>(null);
  const [selectedCaseCost, setSelectedCaseCost] = useState<CaseCost | null>(null);
  const [selectedOutcome, setSelectedOutcome] = useState<OutcomeDetail | null>(null);

  const handleTabChange = (newTab: TabKey) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", newTab);
    startTransition(() => {
      router.push(`${pathname}?${params.toString()}`);
    });
  };

  const handleDaysChange = (newDays: number) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("days", newDays.toString());
    startTransition(() => {
      router.push(`${pathname}?${params.toString()}`);
    });
  };

  return (
    <div className="page-stack">
      {/* Page Header */}
      <header className="page-header">
        <div className="page-header-row">
          <div className="flex min-w-0 items-start gap-3.5">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-cyan-600 text-white shadow-md shadow-cyan-600/20">
              <Activity className="h-6 w-6" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="page-heading">AI 运行监控与下钻分析</h1>
                {isPending && (
                  <RefreshCw className="h-4 w-4 animate-spin text-cyan-600" />
                )}
              </div>
              <p className="page-subtitle">
                实时监控回复 Agent、定时提醒与档案官的执行效果，点击任意数据项可直接下钻查看完整的 Token 结构、聊天上下文与 Log。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 self-start">
            <span className="status-pill pill-neutral font-mono">
              数据更新至 {formatChinaShortDateTime(data.generated_at)}
            </span>
          </div>
        </div>

        {/* Header Tabs & Controls */}
        <div className="mt-5 flex min-w-0 flex-col gap-3 border-t border-slate-100 pt-4 lg:flex-row lg:items-center lg:justify-between">
          <nav
            aria-label="AI 运行分类"
            className="grid grid-cols-2 gap-1 rounded-xl border border-slate-200 bg-slate-100/80 p-1 sm:inline-grid sm:grid-cols-4"
          >
            {TABS.map((item) => {
              const Icon = item.icon;
              const active = initialTab === item.key;
              return (
                <button
                  key={item.key}
                  onClick={() => handleTabChange(item.key)}
                  className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-bold transition-all ${
                    active
                      ? "bg-white text-cyan-900 shadow-sm"
                      : "text-slate-600 hover:bg-white/60 hover:text-slate-950"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  {item.label}
                </button>
              );
            })}
          </nav>

          <div className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white p-1">
            {[7, 30, 90].map((value) => (
              <button
                key={value}
                onClick={() => handleDaysChange(value)}
                className={`inline-flex min-h-9 items-center justify-center rounded-lg px-3.5 text-xs font-bold transition-all ${
                  initialDays === value
                    ? "bg-slate-900 text-white shadow-sm"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                {value} 天
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* Main Tab Contents */}
      {initialTab === "overview" && (
        <OverviewSection
          data={data}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          statusFilter={statusFilter}
          setStatusFilter={setStatusFilter}
          agentFilter={agentFilter}
          setAgentFilter={setAgentFilter}
          onSelectRun={(run) => setSelectedRun(run)}
          onSelectOutcome={(out, total) =>
            setSelectedOutcome({ key: out.key, count: out.count, total })
          }
        />
      )}

      {initialTab === "reply" && (
        <ReplySection
          data={data}
          onSelectTool={(tool) => setSelectedTool(tool)}
          onSelectOutcome={(out, total) =>
            setSelectedOutcome({ key: out.key, count: out.count, total })
          }
        />
      )}

      {initialTab === "archivist" && (
        <ArchivistSection
          data={data}
          onSelectBatch={(batch) => setSelectedRun(batch)}
        />
      )}

      {initialTab === "costs" && (
        <CostsSection
          data={data}
          onSelectScenario={(sc) => setSelectedScenario(sc)}
          onSelectCase={(cc) => setSelectedCaseCost(cc)}
        />
      )}

      {/* Modals & Drawers */}
      <RunDetailDrawer run={selectedRun} onClose={() => setSelectedRun(null)} />
      <ToolDetailModal tool={selectedTool} onClose={() => setSelectedTool(null)} />
      <ScenarioCostModal
        scenario={selectedScenario}
        totalCost={data.costs.estimated_cost_yuan}
        onClose={() => setSelectedScenario(null)}
      />
      <CaseCostDrawer caseCost={selectedCaseCost} onClose={() => setSelectedCaseCost(null)} />
      <OutcomeDetailModal outcome={selectedOutcome} onClose={() => setSelectedOutcome(null)} />
    </div>
  );
}

function OverviewSection({
  data,
  searchQuery,
  setSearchQuery,
  statusFilter,
  setStatusFilter,
  agentFilter,
  setAgentFilter,
  onSelectRun,
  onSelectOutcome,
}: {
  data: AgentOperationsData;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  statusFilter: string;
  setStatusFilter: (s: string) => void;
  agentFilter: string;
  setAgentFilter: (a: string) => void;
  onSelectRun: (run: RecentRun) => void;
  onSelectOutcome: (out: OutcomeCount, total: number) => void;
}) {
  const filteredRuns = data.recent_runs.filter((run) => {
    // Status filter
    if (statusFilter !== "all" && run.status !== statusFilter) return false;
    // Agent filter
    if (agentFilter !== "all" && run.agent_type !== agentFilter) return false;
    // Search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchName = run.user_name?.toLowerCase().includes(q);
      const matchCase = run.case_no?.toLowerCase().includes(q);
      const matchId = run.id.toString().includes(q);
      const matchOutcome = run.outcome?.toLowerCase().includes(q);
      if (!matchName && !matchCase && !matchId && !matchOutcome) return false;
    }
    return true;
  });

  return (
    <>
      <MetricStrip
        items={[
          {
            label: "Agent 总运行数",
            value: formatNumber(data.all_agents.runs),
            detail: `${data.all_agents.running_runs} 次仍在运行`,
            icon: Activity,
          },
          {
            label: "模型请求次数",
            value: formatNumber(data.all_agents.model_calls),
            detail: `${formatNumber(data.all_agents.total_tokens)} Tokens`,
            icon: MessageSquareReply,
          },
          {
            label: "Agent 消耗算力费",
            value: formatCost(data.all_agents.estimated_cost_yuan),
            detail: "回复、提醒与档案官汇总",
            icon: CircleDollarSign,
          },
          {
            label: "运行失败数",
            value: formatNumber(data.all_agents.failed_runs),
            detail: failureDescription(data.all_agents),
            icon: TriangleAlert,
            tone: data.all_agents.failed_runs ? "risk" : "ok",
          },
        ]}
      />

      <div className="grid gap-5 xl:grid-cols-2">
        <OutcomePanel
          title="回复 Agent 的最终去向"
          description="点击任意终态类型可下钻查看详细处置策略与业务含义。"
          outcomes={data.reply.outcomes}
          total={data.reply.runs}
          empty="所选时间内还没有回复 Agent 运行记录。"
          onSelectOutcome={onSelectOutcome}
        />
        <OutputPanel archivist={data.archivist} />
      </div>

      {/* Recent Runs Panel with Interactive Table */}
      <section className="panel overflow-hidden">
        <div className="panel-header-clean flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="panel-title">最近 Agent 运行记录</h2>
              <span className="text-xs bg-cyan-100 text-cyan-800 font-bold px-2 py-0.5 rounded-full flex items-center gap-1">
                <MousePointerClick className="h-3 w-3" /> 点击行可下钻 Log
              </span>
            </div>
            <p className="panel-subtitle">
              快速追踪执行失败、长期运行中或高 Token 消耗的详细 Turn Trace。
            </p>
          </div>

          {/* Controls: Search and Filters */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
              <input
                type="text"
                placeholder="搜索用户 / 服务单号 / ID..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-48 sm:w-64 rounded-xl border border-slate-200 bg-slate-50 pl-9 pr-3 py-1.5 text-xs text-slate-800 focus:bg-white focus:border-cyan-500 focus:outline-none transition-all"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 focus:outline-none"
            >
              <option value="all">所有状态</option>
              <option value="completed">已完成</option>
              <option value="failed">失败</option>
              <option value="running">运行中</option>
            </select>
            <select
              value={agentFilter}
              onChange={(e) => setAgentFilter(e.target.value)}
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 focus:outline-none"
            >
              <option value="all">所有 Agent</option>
              <option value="reply">回复 Agent</option>
              <option value="archivist">档案官</option>
            </select>
          </div>
        </div>

        {filteredRuns.length ? (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>时间 / 关联服务单</th>
                  <th>Agent 类型</th>
                  <th>终态</th>
                  <th>模型 / 工具</th>
                  <th>Tokens 消耗</th>
                  <th>响应耗时</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredRuns.map((run) => (
                  <tr
                    key={run.id}
                    onClick={() => onSelectRun(run)}
                    className="cursor-pointer group hover:bg-cyan-50/60"
                  >
                    <td>
                      <div className="text-xs font-semibold text-slate-900">
                        {formatChinaShortDateTime(run.started_at)}
                      </div>
                      <div className="mt-0.5 text-xs text-slate-500 flex items-center gap-1">
                        <span className="font-bold text-slate-700">
                          {run.user_name || run.case_no || `Run #${run.id}`}
                        </span>
                        {run.case_no && <span className="font-mono text-slate-400">({run.case_no})</span>}
                      </div>
                    </td>
                    <td>
                      <span
                        className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-bold ${
                          run.agent_type === "archivist"
                            ? "bg-amber-50 text-amber-800 border border-amber-200"
                            : "bg-cyan-50 text-cyan-800 border border-cyan-200"
                        }`}
                      >
                        {run.agent_type === "archivist" ? "档案官" : "回复 Agent"}
                      </span>
                      <div className="mt-0.5 text-[11px] text-slate-400 font-mono">{run.run_kind}</div>
                    </td>
                    <td>
                      <RunStatus run={run} />
                      <div className="mt-1 text-xs text-slate-600 font-medium">
                        {OUTCOME_LABELS[run.outcome ?? "running"] ?? run.outcome ?? "运行中"}
                      </div>
                    </td>
                    <td className="tabular text-xs font-bold text-slate-800">
                      {run.model_calls} 次 / {run.tool_calls} 次
                    </td>
                    <td className="tabular text-xs font-mono font-bold text-slate-900">
                      {formatNumber(run.total_tokens)}
                    </td>
                    <td className="text-xs font-mono text-slate-700">
                      {formatLatency(run.latency_ms)}
                    </td>
                    <td className="text-right">
                      <button className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2.5 py-1 text-xs font-bold text-slate-700 group-hover:border-cyan-400 group-hover:text-cyan-800 transition-colors shadow-xs">
                        查看下钻 <ChevronRight className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="px-6 py-12 text-center text-xs text-slate-500">
            没有符合筛选条件的 Agent 运行记录。
          </div>
        )}
      </section>
    </>
  );
}

function ReplySection({
  data,
  onSelectTool,
  onSelectOutcome,
}: {
  data: AgentOperationsData;
  onSelectTool: (tool: ToolStat) => void;
  onSelectOutcome: (out: OutcomeCount, total: number) => void;
}) {
  const sent = outcomeCount(data.reply, "reply_sent");
  const noReply = outcomeCount(data.reply, "intentional_no_reply");
  const handoff =
    outcomeCount(data.reply, "human_handoff") +
    outcomeCount(data.reply, "handoff_already_recorded");

  return (
    <>
      <MetricStrip
        items={[
          {
            label: "对话 Turn 轮次",
            value: formatNumber(data.reply.runs),
            detail: "聚合后的用户消息 Turn 计",
            icon: Bot,
          },
          {
            label: "已发送回复",
            value: formatNumber(sent),
            detail: percentDetail(sent, data.reply.runs),
            icon: CheckCircle2,
            tone: "ok",
          },
          {
            label: "主动不回复",
            value: formatNumber(noReply),
            detail: percentDetail(noReply, data.reply.runs),
            icon: MessageSquareReply,
          },
          {
            label: "触发转人工",
            value: formatNumber(handoff),
            detail: percentDetail(handoff, data.reply.runs),
            icon: TriangleAlert,
            tone: handoff ? "warn" : "neutral",
          },
          {
            label: "响应耗时 P95",
            value: formatLatency(data.reply.latency_p95_ms),
            detail: `P50 ${formatLatency(data.reply.latency_p50_ms)}`,
            icon: Clock3,
          },
        ]}
      />

      <OutcomePanel
        title="完整终态分布"
        description="保留真实业务原因分布，点击任意卡片查看具体的处置建议与处置标准。"
        outcomes={data.reply.outcomes}
        total={data.reply.runs}
        empty="所选时间内还没有回复 Agent 运行记录。"
        onSelectOutcome={onSelectOutcome}
      />

      {/* Interactive Tool Panel */}
      <ToolPanel tools={data.tools} onSelectTool={onSelectTool} />
    </>
  );
}

function ArchivistSection({
  data,
  onSelectBatch,
}: {
  data: AgentOperationsData;
  onSelectBatch: (batch: ArchivistBatch) => void;
}) {
  const useful = outcomeCount(data.archivist, "archived_with_output");
  const noOutput = outcomeCount(data.archivist, "archived_no_output");
  return (
    <>
      <section className="panel overflow-hidden">
        <div className="flex flex-col gap-5 px-5 py-5 sm:px-6">
          <div className="max-w-[75ch]">
            <h2 className="panel-title">档案官抽取与归档统计</h2>
            <p className="panel-subtitle">
              按批次异步读取未归档聊天，并由确定性代码把结构化产出落库。
            </p>
          </div>
          <div className="grid gap-x-6 gap-y-4 border-t border-slate-100 pt-4 sm:grid-cols-2 xl:grid-cols-5">
            <PlainMetric label="归档批次" value={`${formatNumber(data.archivist.runs)} 批`} />
            <PlainMetric label="实际输入" value={`${formatNumber(data.archivist.input_count)} 条聊天`} />
            <PlainMetric label="形成更新" value={`${formatNumber(useful)} 批`} />
            <PlainMetric label="检查后无更新" value={`${formatNumber(noOutput)} 批`} />
            <PlainMetric label="本期模型费" value={formatCost(data.archivist.estimated_cost_yuan)} />
          </div>
        </div>
      </section>

      <ArchivistBatchLedger batches={data.archivist_batches} onSelectBatch={onSelectBatch} />

      <div className="grid gap-5 xl:grid-cols-[1.25fr_0.75fr]">
        <OutputPanel archivist={data.archivist} expanded />
        <DraftReviewPanel statuses={data.archivist.draft_statuses} />
      </div>
    </>
  );
}

function CostsSection({
  data,
  onSelectScenario,
  onSelectCase,
}: {
  data: AgentOperationsData;
  onSelectScenario: (sc: ScenarioCost) => void;
  onSelectCase: (cc: CaseCost) => void;
}) {
  const averageCost = data.costs.calls
    ? data.costs.estimated_cost_yuan / data.costs.calls
    : 0;

  return (
    <>
      <section className="panel overflow-hidden">
        <div className="grid gap-5 px-5 py-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
          <div>
            <p className="text-sm font-semibold text-slate-600">所选时间内的模型费用估算</p>
            <div className="tabular mt-1 text-3xl font-black tracking-tight text-slate-950">
              {formatCost(data.costs.estimated_cost_yuan)}
            </div>
            <p className="mt-2 max-w-[75ch] text-sm leading-6 text-slate-600">
              只统计大模型请求，不含服务器、企微通道等费用。点击下方动作或用户可直接下钻结构与比例。
            </p>
          </div>
          <div className="grid grid-cols-2 gap-x-8 gap-y-3 border-t border-slate-100 pt-4 sm:grid-cols-3 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
            <PlainMetric label="模型请求" value={`${formatNumber(data.costs.calls)} 次`} />
            <PlainMetric label="平均每次" value={formatCost(averageCost)} />
            <PlainMetric label="涉及服务单" value={`${formatNumber(data.costs.service_case_count)} 个`} />
          </div>
        </div>
      </section>

      <ScenarioCosts
        scenarios={data.costs.scenarios}
        totalCost={data.costs.estimated_cost_yuan}
        onSelectScenario={onSelectScenario}
      />
      <CaseCosts cases={data.costs.cases} onSelectCase={onSelectCase} />
    </>
  );
}

function MetricStrip({
  items,
}: {
  items: Array<{
    label: string;
    value: string;
    detail: string;
    icon: typeof Activity;
    tone?: "ok" | "warn" | "risk" | "neutral";
  }>;
}) {
  return (
    <section className="panel overflow-hidden" aria-label="关键指标">
      <div className="grid gap-px bg-slate-100 sm:grid-cols-2 xl:grid-cols-4">
        {items.map((item) => {
          const Icon = item.icon;
          const tone = item.tone ?? "neutral";
          const iconTone = {
            ok: "bg-emerald-50 text-emerald-700",
            warn: "bg-amber-50 text-amber-700",
            risk: "bg-rose-50 text-rose-700",
            neutral: "bg-cyan-50 text-cyan-700",
          }[tone];
          return (
            <div key={item.label} className="flex min-w-0 items-start gap-3.5 bg-white p-5">
              <span
                className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl ${iconTone}`}
              >
                <Icon className="h-5 w-5" />
              </span>
              <div className="min-w-0">
                <div className="text-xs font-bold uppercase tracking-wide text-slate-400">
                  {item.label}
                </div>
                <div className="tabular mt-1 text-2xl font-black tracking-tight text-slate-950">
                  {item.value}
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-500">{item.detail}</p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function OutcomePanel({
  title,
  description,
  outcomes,
  total,
  empty,
  onSelectOutcome,
}: {
  title: string;
  description: string;
  outcomes: OutcomeCount[];
  total: number;
  empty: string;
  onSelectOutcome: (out: OutcomeCount, total: number) => void;
}) {
  const max = Math.max(...outcomes.map((item) => item.count), 1);
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">{title}</h2>
        <p className="panel-subtitle">{description}</p>
      </div>
      {outcomes.length ? (
        <div className="divide-y divide-slate-100">
          {outcomes.map((item) => (
            <div
              key={item.key}
              onClick={() => onSelectOutcome(item, total)}
              className="grid min-w-0 gap-2 px-5 py-3.5 sm:grid-cols-[minmax(0,14rem)_minmax(8rem,1fr)_5rem] sm:items-center sm:px-6 cursor-pointer hover:bg-cyan-50/50 transition-colors group"
            >
              <div className="min-w-0">
                <div className="text-sm font-bold text-slate-900 group-hover:text-cyan-900 flex items-center gap-1">
                  {OUTCOME_LABELS[item.key] ?? item.key}
                  <Info className="h-3.5 w-3.5 text-slate-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
                <div className="mt-0.5 text-xs text-slate-400 font-mono">{item.key}</div>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full bg-cyan-600 group-hover:bg-cyan-500 transition-all"
                  style={{ width: `${Math.max(3, (item.count / max) * 100)}%` }}
                />
              </div>
              <div className="tabular text-right text-sm font-black text-slate-900">
                {item.count}
                <span className="ml-1.5 text-xs font-medium text-slate-400">
                  {total ? `${Math.round((item.count / total) * 100)}%` : "—"}
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-500">{empty}</div>
      )}
    </section>
  );
}

function OutputPanel({
  archivist,
  expanded = false,
}: {
  archivist: ArchivistSummary;
  expanded?: boolean;
}) {
  const entries = Object.entries(archivist.output_breakdown).sort(
    (a, b) => b[1] - a[1],
  );
  const max = Math.max(...entries.map(([, value]) => value), 1);
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">档案官确定性产出分布</h2>
        <p className="panel-subtitle">
          校验并持久化写入数据库的业务档案事实统计。
        </p>
      </div>
      {entries.length ? (
        <div className="divide-y divide-slate-100">
          {entries.slice(0, expanded ? entries.length : 6).map(([key, value]) => (
            <div key={key} className="grid grid-cols-[minmax(0,1fr)_4rem] items-center gap-4 px-5 py-3.5 sm:px-6">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-slate-800">
                  {OUTPUT_LABELS[key] ?? key}
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-emerald-500"
                    style={{ width: `${Math.max(3, (value / max) * 100)}%` }}
                  />
                </div>
              </div>
              <div className="tabular text-right text-base font-black text-slate-900">
                {value}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-500">
          所选时间内还没有已写入的档案产出。
        </div>
      )}
    </section>
  );
}

function DraftReviewPanel({ statuses }: { statuses: Record<string, number> }) {
  const total = statuses.total ?? 0;
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">FAQ / 案例草稿后验结果</h2>
        <p className="panel-subtitle">人工审核反馈是衡量提议质量的直接指标。</p>
      </div>
      <div className="divide-y divide-slate-100">
        <InlineMetric label="草稿总数" value={total} />
        <InlineMetric label="待审核" value={statuses.pending ?? 0} />
        <InlineMetric label="已通过" value={statuses.approved ?? 0} />
        <InlineMetric label="已拒绝" value={statuses.rejected ?? 0} />
      </div>
      <div className="border-t border-slate-100 px-5 py-4 sm:px-6">
        <Link href="/documents" className="admin-link text-xs flex items-center gap-1 font-bold">
          前往审核控制台 <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </section>
  );
}

function ToolPanel({
  tools,
  onSelectTool,
}: {
  tools: ToolStat[];
  onSelectTool: (tool: ToolStat) => void;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <Wrench className="h-4 w-4 text-cyan-700" />
            <h2 className="panel-title">工具调用表现统计</h2>
            <span className="text-xs bg-cyan-100 text-cyan-800 font-bold px-2 py-0.5 rounded-full flex items-center gap-1">
              <MousePointerClick className="h-3 w-3" /> 点击下钻工具详情
            </span>
          </div>
          <p className="panel-subtitle">
            监控工具成功率、平均响应耗时与异常错误分布。
          </p>
        </div>
      </div>

      {tools.length ? (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>工具名称</th>
                <th>调用次数</th>
                <th>成功率</th>
                <th>失败 / 拒绝</th>
                <th>平均耗时</th>
                <th>主要错误</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((tool) => (
                <tr
                  key={tool.name}
                  onClick={() => onSelectTool(tool)}
                  className="cursor-pointer group hover:bg-cyan-50/60"
                >
                  <td>
                    <div className="font-bold text-slate-900 group-hover:text-cyan-900">
                      {TOOL_LABELS[tool.name] ?? tool.name}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-400 font-mono">{tool.name}</div>
                  </td>
                  <td className="tabular font-bold text-slate-800">{tool.calls} 次</td>
                  <td className="tabular">
                    <span
                      className={`font-bold ${
                        (tool.success_rate ?? 0) >= 90
                          ? "text-emerald-700"
                          : (tool.success_rate ?? 0) >= 70
                          ? "text-amber-700"
                          : "text-rose-700"
                      }`}
                    >
                      {tool.success_rate == null ? "—" : `${tool.success_rate}%`}
                    </span>
                  </td>
                  <td className="tabular text-xs text-slate-700 font-mono">
                    {tool.failed_calls} / {tool.rejected_calls}
                  </td>
                  <td className="text-xs font-mono">{formatLatency(tool.average_latency_ms)}</td>
                  <td className="max-w-[16rem] text-xs text-rose-600">
                    {tool.errors.length
                      ? tool.errors.map((item) => `${item.key} × ${item.count}`).join(" · ")
                      : "—"}
                  </td>
                  <td className="text-right">
                    <button className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2.5 py-1 text-xs font-bold text-slate-700 group-hover:border-cyan-400 group-hover:text-cyan-800 transition-colors shadow-xs">
                      详情 <ChevronRight className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-500">
          所选时间内没有工具调用记录。
        </div>
      )}
    </section>
  );
}

function ScenarioCosts({
  scenarios,
  totalCost,
  onSelectScenario,
}: {
  scenarios: ScenarioCost[];
  totalCost: number;
  onSelectScenario: (sc: ScenarioCost) => void;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">按业务场景的算力支出拆解</h2>
        <p className="panel-subtitle">点击任意场景可查看具体 Prompt / Completion Token 结构。</p>
      </div>
      {scenarios.length ? (
        <div className="divide-y divide-slate-100">
          {scenarios.map((item) => {
            const average = item.calls ? item.estimated_cost_yuan / item.calls : 0;
            const share = totalCost ? (item.estimated_cost_yuan / totalCost) * 100 : 0;
            return (
              <div
                key={item.scenario}
                onClick={() => onSelectScenario(item)}
                className="px-5 py-4 sm:px-6 cursor-pointer hover:bg-cyan-50/50 transition-colors group"
              >
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_repeat(3,minmax(7rem,auto))] lg:items-center">
                  <div className="max-w-[65ch]">
                    <div className="font-bold text-slate-900 group-hover:text-cyan-900 flex items-center gap-1.5">
                      {item.scenario}
                      <ChevronRight className="h-4 w-4 text-slate-400 group-hover:text-cyan-700" />
                    </div>
                  </div>
                  <SmallDatum label="模型请求" value={`${formatNumber(item.calls)} 次`} />
                  <SmallDatum label="合计花费" value={formatCost(item.estimated_cost_yuan)} />
                  <SmallDatum label="平均每次" value={formatCost(average)} />
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-cyan-600 group-hover:bg-cyan-500 transition-all"
                      style={{ width: `${Math.max(2, share)}%` }}
                    />
                  </div>
                  <span className="tabular w-16 text-right text-xs font-bold text-slate-700">
                    占 {share.toFixed(1)}%
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-600">
          所选时间内没有 AI 成本记录。
        </div>
      )}
    </section>
  );
}

function CaseCosts({
  cases,
  onSelectCase,
}: {
  cases: CaseCost[];
  onSelectCase: (cc: CaseCost) => void;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">按服务单 / 用户的 AI 支出分布</h2>
        <p className="panel-subtitle">点击任意服务单可下钻查看详细场景消耗对比。</p>
      </div>
      {cases.length ? (
        <div className="divide-y divide-slate-100">
          {cases.map((item) => (
            <div
              key={item.service_case_id}
              onClick={() => onSelectCase(item)}
              className="px-5 py-4 sm:px-6 cursor-pointer hover:bg-cyan-50/50 transition-colors group"
            >
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_repeat(3,minmax(7rem,auto))_1.5rem] sm:items-center">
                <div>
                  <div className="font-bold text-slate-950 group-hover:text-cyan-900">
                    {item.user_name || "未命名用户"}
                  </div>
                  <div className="mt-0.5 text-xs text-slate-500 font-mono">{item.case_no}</div>
                </div>
                <SmallDatum label="模型请求" value={`${formatNumber(item.calls)} 次`} />
                <SmallDatum label="合计花费" value={formatCost(item.estimated_cost_yuan)} />
                <SmallDatum
                  label="最后发生"
                  value={formatChinaShortDateTime(item.last_call_at)}
                />
                <ChevronRight className="h-5 w-5 text-slate-400 group-hover:text-cyan-700" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-600">
          所选时间内没有关联服务单的成本记录。
        </div>
      )}
    </section>
  );
}

function ArchivistBatchLedger({
  batches,
  onSelectBatch,
}: {
  batches: ArchivistBatch[];
  onSelectBatch: (batch: ArchivistBatch) => void;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">逐批归档记录</h2>
        <p className="panel-subtitle">点击任意批次可查看完整的输入聊天原文与落库变动。</p>
      </div>
      {batches.length ? (
        <div className="divide-y divide-slate-100">
          {batches.map((batch) => (
            <div
              key={batch.id}
              onClick={() => onSelectBatch(batch)}
              className="px-5 py-4 sm:px-6 cursor-pointer hover:bg-cyan-50/50 transition-colors group flex items-center justify-between"
            >
              <div>
                <div className="flex items-center gap-2">
                  <RunStatus run={batch} />
                  <span className="font-bold text-slate-900 group-hover:text-cyan-900">
                    {batch.user_name || batch.case_no || "未知服务单"}
                  </span>
                  <span className="text-xs text-slate-400 font-mono">
                    {formatChinaShortDateTime(batch.started_at)}
                  </span>
                </div>
                <div className="mt-1.5 text-xs text-slate-600">
                  输入 {batch.input_messages?.length || batch.input_count || 0} 条消息 → 写入{" "}
                  {batch.changes?.length || batch.output_count || 0} 项变更
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs font-mono font-bold text-slate-800">
                  {formatCost(batch.estimated_cost_yuan)}
                </span>
                <ChevronRight className="h-5 w-5 text-slate-400 group-hover:text-cyan-700" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-600">
          所选时间内暂无档案官批次。
        </div>
      )}
    </section>
  );
}

function RunStatus({ run }: { run: RecentRun }) {
  const tone =
    run.status === "completed"
      ? "pill-ok"
      : run.status === "failed"
      ? "pill-risk"
      : "pill-warn";
  const label =
    run.status === "completed"
      ? "已完成"
      : run.status === "failed"
      ? "失败"
      : "运行中";
  return <span className={`status-pill ${tone}`}>{label}</span>;
}

function InlineMetric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="flex items-center justify-between gap-4 px-5 py-4 sm:block sm:px-6">
      <div className="text-sm font-semibold text-slate-600">{label}</div>
      <div className="tabular mt-1 text-xl font-black text-slate-950">{value}</div>
    </div>
  );
}

function PlainMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-semibold text-slate-500">{label}</div>
      <div className="tabular mt-1 text-lg font-black text-slate-900">{value}</div>
    </div>
  );
}

function SmallDatum({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-semibold text-slate-500">{label}</div>
      <div className="tabular mt-0.5 text-xs font-bold text-slate-800">{value}</div>
    </div>
  );
}

function outcomeCount(summary: RuntimeSummary, key: string) {
  return summary.outcomes.find((item) => item.key === key)?.count ?? 0;
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value || 0);
}

function formatCost(value: number) {
  const digits = value > 0 && value < 0.01 ? 4 : 2;
  return `¥${(value || 0).toFixed(digits)}`;
}

function formatLatency(value?: number | null) {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
}

function failureDescription(summary: RuntimeSummary) {
  if (!summary.runs) return "暂无运行记录";
  if (!summary.failed_runs) return "所选时间内无失败";
  return `${((summary.failed_runs / summary.runs) * 100).toFixed(1)}% 的运行失败`;
}

function percentDetail(value: number, total: number) {
  return total ? `占全部轮次 ${((value / total) * 100).toFixed(1)}%` : "暂无样本";
}
