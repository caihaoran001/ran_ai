"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import {
  Activity,
  Archive,
  Bot,
  CheckCircle2,
  Clock3,
  Coins,
  UserCheck,
  PauseCircle,
  TriangleAlert,
  Wrench,
  Search,
  RefreshCw,
  ChevronRight,
  ChevronDown,
  ArrowRight,
  Send,
  Wallet,
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
  { key: "reply", label: "群内回复", icon: Bot },
  { key: "archivist", label: "整理档案", icon: Archive },
  { key: "costs", label: "花费", icon: Coins },
] as const;
type TabKey = (typeof TABS)[number]["key"];

// 每种处理结果对应一句人话（点开某一类时展开显示的明细）。
const OUTCOME_LABELS: Record<string, string> = {
  reply_sent: "正常回复了用户",
  reply_already_recorded: "回复已经发过",
  intentional_no_reply: "判断无需插话",
  human_handoff: "已通知班主任接手",
  handoff_already_recorded: "已通知过班主任",
  handoff_throttled: "刚提醒过，暂不重复打扰",
  staff_preempted: "班主任先回复了",
  stale_reply_suppressed: "用户又发了新消息，旧回复作废",
  newer_message_queued: "已有更新的消息在排队",
  ai_disabled: "全局自动回复被关闭",
  ai_disabled_before_send: "发送前全局开关被关",
  case_auto_reply_disabled: "该用户的自动回复被关闭",
  case_disabled_before_send: "发送前该用户开关被关",
  inactive_case: "服务已结束",
  no_active_case: "没有进行中的服务",
  non_chat_message: "不是聊天消息",
  send_policy_suppressed: "被发送策略拦下",
  agent_generation_failed: "回复没能生成",
  send_failed: "回复没发出去",
  send_unknown: "发送结果不明",
  workflow_completed: "流程正常结束",
  archived_with_output: "整理出了新信息",
  archived_no_output: "检查后没有新信息",
  archivist_failed: "整理失败",
  archivist_replay_failed: "人工重跑失败",
  reminder_sent: "提醒已发出",
  reminder_generation_failed: "提醒没能生成",
  reminder_empty: "提醒内容为空",
  reminder_send_failed: "提醒没发出去",
  running: "处理中",
};

// 24 种细粒度结果 → 5 个运营看得懂的大类。未知 key 兜底进「没轮到 AI 出手」。
type BucketTone = "ok" | "info" | "warn" | "neutral" | "risk";
const OUTCOME_BUCKETS: Array<{
  id: string;
  label: string;
  hint: string;
  tone: BucketTone;
  icon: typeof CheckCircle2;
  keys: string[];
}> = [
  {
    id: "replied",
    label: "已回复用户",
    hint: "AI 正常给出了答复",
    tone: "ok",
    icon: CheckCircle2,
    keys: ["reply_sent", "reply_already_recorded", "workflow_completed"],
  },
  {
    id: "no_reply",
    label: "判断无需回复",
    hint: "AI 认为不用插话，比如打招呼收尾、纯表情、班主任已在沟通",
    tone: "info",
    icon: Bot,
    keys: ["intentional_no_reply"],
  },
  {
    id: "handoff",
    label: "转给了人工",
    hint: "涉及风险或强烈情绪，已通知班主任 / 客服接手",
    tone: "warn",
    icon: UserCheck,
    keys: ["human_handoff", "handoff_already_recorded", "handoff_throttled"],
  },
  {
    id: "skipped",
    label: "没轮到 AI 出手",
    hint: "班主任先回了、消息被更新，或自动回复被关掉等",
    tone: "neutral",
    icon: PauseCircle,
    keys: [
      "staff_preempted",
      "stale_reply_suppressed",
      "newer_message_queued",
      "ai_disabled",
      "ai_disabled_before_send",
      "case_auto_reply_disabled",
      "case_disabled_before_send",
      "inactive_case",
      "no_active_case",
      "non_chat_message",
      "send_policy_suppressed",
    ],
  },
  {
    id: "failed",
    label: "出问题了",
    hint: "回复没能生成或没发出去，建议排查",
    tone: "risk",
    icon: TriangleAlert,
    keys: ["agent_generation_failed", "send_failed", "send_unknown"],
  },
];

const BUCKET_OF_KEY: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const bucket of OUTCOME_BUCKETS) {
    for (const key of bucket.keys) map[key] = bucket.id;
  }
  return map;
})();

type Bucket = {
  id: string;
  label: string;
  hint: string;
  tone: BucketTone;
  icon: typeof CheckCircle2;
  count: number;
  reasons: OutcomeCount[];
};

function buildBuckets(outcomes: OutcomeCount[]): Bucket[] {
  const byId: Record<string, Bucket> = {};
  for (const def of OUTCOME_BUCKETS) {
    byId[def.id] = { ...def, count: 0, reasons: [] };
  }
  for (const item of outcomes) {
    const bucketId = BUCKET_OF_KEY[item.key] ?? "skipped";
    const bucket = byId[bucketId];
    bucket.count += item.count;
    bucket.reasons.push(item);
  }
  for (const bucket of Object.values(byId)) {
    bucket.reasons.sort((a, b) => b.count - a.count);
  }
  return OUTCOME_BUCKETS.map((def) => byId[def.id]);
}

const TONE_STYLES: Record<
  BucketTone,
  { icon: string; bar: string; text: string }
> = {
  ok: { icon: "bg-emerald-50 text-emerald-700", bar: "bg-emerald-500", text: "text-emerald-700" },
  info: { icon: "bg-cyan-50 text-cyan-700", bar: "bg-cyan-500", text: "text-cyan-700" },
  warn: { icon: "bg-amber-50 text-amber-700", bar: "bg-amber-500", text: "text-amber-700" },
  neutral: { icon: "bg-slate-100 text-slate-600", bar: "bg-slate-400", text: "text-slate-600" },
  risk: { icon: "bg-rose-50 text-rose-700", bar: "bg-rose-500", text: "text-rose-700" },
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
  web_search_lifestyle: "上网查资料",
};

const OUTPUT_LABELS: Record<string, string> = {
  checkin_days: "打卡记录",
  outcomes: "阶段成效",
  profile_facts: "长期画像",
  state_facts: "当前状态",
  personal_guidance: "个人指导",
  corrections: "班主任纠正",
  faq_drafts: "问答草稿",
  memory_updates: "小结更新",
  user_summary_updates: "用户历程小结",
  case_material_updates: "优秀案例素材",
  case_drafts: "优秀案例草稿",
};

export type DeepseekBalance = {
  available: boolean;
  balance_yuan: number | null;
  currency: string;
  is_available: boolean;
  is_low: boolean;
  alert_threshold_yuan: number;
  fetched_at?: string | null;
};

export function AgentOperationsClient({
  data,
  initialTab,
  initialDays,
  balance,
}: {
  data: AgentOperationsData;
  initialTab: TabKey;
  initialDays: number;
  balance?: DeepseekBalance | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [isPending, startTransition] = useTransition();
  const [pendingControl, setPendingControl] = useState<string | null>(null);

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

  const handleSelectRun = (run: RunDetailData) => {
    // If it's an archivist run, find its full batch data in archivist_batches if available
    if (run.agent_type === "archivist") {
      const matchedBatch = data.archivist_batches.find((b) => b.id === run.id);
      if (matchedBatch) {
        setSelectedRun({ ...run, ...matchedBatch });
        return;
      }
    }
    if (run.agent_type !== "reply") {
      setSelectedRun(run);
      return;
    }

    setSelectedRun({
      ...run,
      detail_loading: true,
      detail_error: null,
    });
    void fetch(`/api/backend/agent-operations/runs/${run.id}`, {
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`详情接口返回 ${response.status}`);
        }
        return (await response.json()) as RunDetailData;
      })
      .then((detail) => {
        setSelectedRun((current) =>
          current?.id === run.id ? detail : current,
        );
      })
      .catch((error: unknown) => {
        const message =
          error instanceof Error ? error.message : "未知网络错误";
        setSelectedRun((current) =>
          current?.id === run.id
            ? {
                ...current,
                detail_loading: false,
                detail_error: `${message}，请关闭后重新打开详情。`,
              }
            : current,
        );
      });
  };

  const handleTabChange = (newTab: TabKey) => {
    if (newTab === initialTab || isPending) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", newTab);
    setPendingControl(`tab:${newTab}`);
    startTransition(() => {
      router.push(`${pathname}?${params.toString()}`);
    });
  };

  const handleDaysChange = (newDays: number) => {
    if (newDays === initialDays || isPending) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set("days", newDays.toString());
    setPendingControl(`days:${newDays}`);
    startTransition(() => {
      router.push(`${pathname}?${params.toString()}`);
    });
  };

  useEffect(() => {
    if (!isPending) setPendingControl(null);
  }, [isPending]);

  const rangeLabel =
    initialDays === 1 ? "近24h" : `近${initialDays}天`;

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
                <h1 className="page-heading">AI 助教运行情况</h1>
                {isPending && (
                  <RefreshCw className="h-4 w-4 animate-spin text-cyan-600" />
                )}
              </div>
              <p className="page-subtitle">
                看看这段时间 AI 助教在群里干了多少活、有没有需要你处理的情况，以及花了多少钱。任意一项都能点开看得更细。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 self-start">
            <span className="status-pill pill-neutral">
              数据截至 {formatChinaShortDateTime(data.generated_at)}
            </span>
          </div>
        </div>

        {/* Header Tabs & Controls */}
        <div className="mt-5 flex min-w-0 flex-col gap-3 border-t border-slate-100 pt-4 lg:flex-row lg:items-center lg:justify-between">
          <nav
            aria-label="查看分类"
            className="grid grid-cols-2 gap-1 rounded-xl border border-slate-200 bg-slate-100/80 p-1 sm:inline-grid sm:grid-cols-4"
          >
            {TABS.map((item) => {
              const Icon = item.icon;
              const active = initialTab === item.key;
              return (
                <button
                  key={item.key}
                  onClick={() => handleTabChange(item.key)}
                  disabled={isPending}
                  aria-busy={pendingControl === `tab:${item.key}` || undefined}
                  className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-bold transition-all ${
                    active
                      ? "bg-white text-cyan-900 shadow-sm"
                      : "text-slate-600 hover:bg-white/60 hover:text-slate-950"
                  } disabled:cursor-wait disabled:opacity-60`}
                >
                  {pendingControl === `tab:${item.key}` ? (
                    <RefreshCw className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                  ) : (
                    <Icon className="h-4 w-4" />
                  )}
                  {pendingControl === `tab:${item.key}` ? "加载中…" : item.label}
                </button>
              );
            })}
          </nav>

          <div className="grid w-full grid-cols-5 items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 sm:flex sm:w-auto sm:gap-1.5">
            {[
              { value: 1, label: "近24h" },
              { value: 3, label: "近3天" },
              { value: 7, label: "近7天" },
              { value: 30, label: "近30天" },
              { value: 90, label: "近90天" },
            ].map(({ value, label }) => (
              <button
                key={value}
                onClick={() => handleDaysChange(value)}
                disabled={isPending}
                aria-pressed={initialDays === value}
                aria-busy={pendingControl === `days:${value}` || undefined}
                className={`inline-flex min-h-10 min-w-0 items-center justify-center whitespace-nowrap rounded-lg px-1 text-xs font-bold transition-all sm:px-3.5 ${
                  initialDays === value
                    ? "bg-slate-900 text-white shadow-sm"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                } disabled:cursor-wait disabled:opacity-60`}
              >
                {pendingControl === `days:${value}` && (
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                )}
                {pendingControl === `days:${value}` ? "加载中…" : label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* Main Tab Contents */}
      {initialTab === "overview" && (
        <OverviewSection
          data={data}
          rangeLabel={rangeLabel}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          statusFilter={statusFilter}
          setStatusFilter={setStatusFilter}
          agentFilter={agentFilter}
          setAgentFilter={setAgentFilter}
          onSelectRun={handleSelectRun}
          onSelectReason={(key, total) =>
            setSelectedOutcome({ key, count: reasonCount(data.reply, key), total })
          }
        />
      )}

      {initialTab === "reply" && (
        <ReplySection
          data={data}
          onSelectTool={(tool) => setSelectedTool(tool)}
          onSelectReason={(key, total) =>
            setSelectedOutcome({ key, count: reasonCount(data.reply, key), total })
          }
        />
      )}

      {initialTab === "archivist" && (
        <ArchivistSection
          data={data}
          onSelectBatch={handleSelectRun}
        />
      )}

      {initialTab === "costs" && (
        <CostsSection
          data={data}
          balance={balance}
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
  rangeLabel,
  searchQuery,
  setSearchQuery,
  statusFilter,
  setStatusFilter,
  agentFilter,
  setAgentFilter,
  onSelectRun,
  onSelectReason,
}: {
  data: AgentOperationsData;
  rangeLabel: string;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  statusFilter: string;
  setStatusFilter: (s: string) => void;
  agentFilter: string;
  setAgentFilter: (a: string) => void;
  onSelectRun: (run: RecentRun) => void;
  onSelectReason: (key: string, total: number) => void;
}) {
  const buckets = useMemo(() => buildBuckets(data.reply.outcomes), [data.reply.outcomes]);
  const replied = buckets.find((b) => b.id === "replied")?.count ?? 0;
  const needsAttention =
    (buckets.find((b) => b.id === "handoff")?.count ?? 0) +
    (buckets.find((b) => b.id === "failed")?.count ?? 0);

  const filteredRuns = data.recent_runs.filter((run) => {
    if (statusFilter !== "all" && run.status !== statusFilter) return false;
    if (agentFilter !== "all" && run.agent_type !== agentFilter) return false;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchName = run.user_name?.toLowerCase().includes(q);
      const matchCase = run.case_no?.toLowerCase().includes(q);
      const matchId = run.id.toString().includes(q);
      if (!matchName && !matchCase && !matchId) return false;
    }
    return true;
  });

  return (
    <>
      <MetricStrip
        items={[
          {
            label: "处理的对话",
            value: formatNumber(data.reply.runs),
            detail: `${rangeLabel}群里由 AI 处理的用户消息`,
            icon: Bot,
          },
          {
            label: "已回复用户",
            value: formatNumber(replied),
            detail: percentDetail(replied, data.reply.runs, "占全部对话"),
            icon: CheckCircle2,
            tone: "ok",
          },
          {
            label: "需要你关注",
            value: formatNumber(needsAttention),
            detail:
              needsAttention > 0
                ? "转人工与出问题的合计，建议查看"
                : "没有转人工或出错，一切正常",
            icon: TriangleAlert,
            tone: needsAttention > 0 ? "warn" : "ok",
          },
          {
            label: "这段时间花费",
            value: formatCost(data.costs.estimated_cost_yuan),
            detail: "所有 AI 功能的算力费合计",
            icon: Coins,
          },
        ]}
      />

      <OutcomeBuckets
        title="AI 是怎么处理这些对话的"
        description="按处理方式分成几大类，点开任意一类可以看具体原因。"
        buckets={buckets}
        total={data.reply.runs}
        onSelectReason={onSelectReason}
      />

      {/* Recent Runs Panel */}
      <section className="panel overflow-hidden">
        <div className="panel-header-clean flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h2 className="panel-title">最近的处理记录</h2>
            <p className="panel-subtitle">
              最新的一批处理记录，点任意一行可以看这次的完整对话和结果。
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
              <input
                type="text"
                placeholder="搜用户名 / 服务单号"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-44 sm:w-56 rounded-xl border border-slate-200 bg-slate-50 pl-9 pr-3 py-1.5 text-xs text-slate-800 focus:bg-white focus:border-cyan-500 focus:outline-none transition-all"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 focus:outline-none"
            >
              <option value="all">全部状态</option>
              <option value="completed">已完成</option>
              <option value="failed">出错</option>
              <option value="running">处理中</option>
            </select>
            <select
              value={agentFilter}
              onChange={(e) => setAgentFilter(e.target.value)}
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 focus:outline-none"
            >
              <option value="all">全部类型</option>
              <option value="reply">群内回复</option>
              <option value="archivist">整理档案</option>
            </select>
          </div>
        </div>

        {filteredRuns.length ? (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>时间 / 对象</th>
                  <th>类型</th>
                  <th>结果</th>
                  <th>耗时</th>
                  <th>花费</th>
                  <th className="w-10" aria-label="查看" />
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
                      <div className="mt-0.5 text-xs text-slate-500">
                        {run.user_name || run.case_no || "系统任务"}
                      </div>
                    </td>
                    <td>
                      <AgentTypeBadge type={run.agent_type} />
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <StatusDot status={run.status} />
                        <span className="text-xs font-medium text-slate-700">
                          {OUTCOME_LABELS[run.outcome ?? "running"] ?? "处理中"}
                        </span>
                      </div>
                    </td>
                    <td className="text-xs text-slate-600">
                      {formatLatency(run.latency_ms)}
                    </td>
                    <td className="tabular text-xs font-semibold text-slate-800">
                      {formatCost(run.estimated_cost_yuan)}
                    </td>
                    <td className="text-right">
                      <ChevronRight className="h-4 w-4 text-slate-300 group-hover:text-cyan-600 transition-colors" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="px-6 py-12 text-center text-sm text-slate-500">
            没有符合条件的处理记录。
          </div>
        )}
      </section>
    </>
  );
}

function ReplySection({
  data,
  onSelectTool,
  onSelectReason,
}: {
  data: AgentOperationsData;
  onSelectTool: (tool: ToolStat) => void;
  onSelectReason: (key: string, total: number) => void;
}) {
  const buckets = useMemo(() => buildBuckets(data.reply.outcomes), [data.reply.outcomes]);
  const replied = buckets.find((b) => b.id === "replied")?.count ?? 0;
  const noReply = buckets.find((b) => b.id === "no_reply")?.count ?? 0;
  const handoff = buckets.find((b) => b.id === "handoff")?.count ?? 0;

  return (
    <>
      <MetricStrip
        items={[
          {
            label: "处理的对话",
            value: formatNumber(data.reply.runs),
            detail: "群里由 AI 处理的用户消息",
            icon: Bot,
          },
          {
            label: "已回复用户",
            value: formatNumber(replied),
            detail: percentDetail(replied, data.reply.runs, "占全部对话"),
            icon: CheckCircle2,
            tone: "ok",
          },
          {
            label: "判断无需回复",
            value: formatNumber(noReply),
            detail: percentDetail(noReply, data.reply.runs, "占全部对话"),
            icon: PauseCircle,
          },
          {
            label: "转给人工",
            value: formatNumber(handoff),
            detail: percentDetail(handoff, data.reply.runs, "占全部对话"),
            icon: UserCheck,
            tone: handoff ? "warn" : "neutral",
          },
          {
            label: "回复速度",
            value: formatLatency(data.reply.latency_p50_ms),
            detail: `较慢时约 ${formatLatency(data.reply.latency_p95_ms)}`,
            icon: Clock3,
          },
        ]}
      />

      {data.reminders.runs > 0 && (
        <div className="flex items-center gap-2.5 rounded-2xl border border-slate-200/80 bg-white px-5 py-3.5 text-sm text-slate-600" style={{ boxShadow: "var(--shadow-panel)" }}>
          <Send className="h-4 w-4 shrink-0 text-cyan-600" />
          <span>
            另外，AI 还主动发出了{" "}
            <span className="font-bold text-slate-900">
              {formatNumber(reasonCount(data.reminders, "reminder_sent"))}
            </span>{" "}
            条定时提醒（打卡关怀、进度提醒等）。
          </span>
        </div>
      )}

      <OutcomeBuckets
        title="AI 是怎么处理这些对话的"
        description="按处理方式分成几大类，点开任意一类可以看具体原因。"
        buckets={buckets}
        total={data.reply.runs}
        onSelectReason={onSelectReason}
      />

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
  const useful = reasonCount(data.archivist, "archived_with_output");
  const noOutput = reasonCount(data.archivist, "archived_no_output");
  return (
    <>
      <section className="panel overflow-hidden">
        <div className="flex flex-col gap-5 px-5 py-5 sm:px-6">
          <div className="max-w-[72ch]">
            <h2 className="panel-title">AI 定期整理群聊里的有用信息</h2>
            <p className="panel-subtitle">
              AI 会隔一段时间把群里的聊天翻一遍，自动把打卡、成效、用户情况等信息整理进档案，不用人工录入。
            </p>
          </div>
          <div className="grid gap-x-6 gap-y-4 border-t border-slate-100 pt-4 sm:grid-cols-2 xl:grid-cols-4">
            <PlainMetric label="整理次数" value={`${formatNumber(data.archivist.runs)} 次`} />
            <PlainMetric label="读过的聊天" value={`${formatNumber(data.archivist.input_count)} 条`} />
            <PlainMetric label="整理出新信息" value={`${formatNumber(useful)} 次`} detail={`另有 ${formatNumber(noOutput)} 次检查后没有新增`} />
            <PlainMetric label="这段时间花费" value={formatCost(data.archivist.estimated_cost_yuan)} />
          </div>
        </div>
      </section>

      <ArchivistBatchLedger batches={data.archivist_batches} onSelectBatch={onSelectBatch} />

      <div className="grid gap-5 xl:grid-cols-[1.25fr_0.75fr]">
        <OutputPanel archivist={data.archivist} />
        <DraftReviewPanel statuses={data.archivist.draft_statuses} />
      </div>
    </>
  );
}

function AccountBalancePanel({ balance }: { balance?: DeepseekBalance | null }) {
  const available = balance?.available ?? false;
  const value = balance?.balance_yuan ?? null;
  const threshold = balance?.alert_threshold_yuan ?? 0;
  const low =
    (balance?.is_low ?? false) || (available && balance?.is_available === false);
  const alert = !available || low;

  return (
    <section className="panel overflow-hidden">
      <div className="grid gap-5 px-5 py-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div>
          <div className="flex items-center gap-2">
            <span
              className={`flex h-9 w-9 items-center justify-center rounded-lg ${
                alert ? "bg-rose-50 text-rose-700" : "bg-cyan-50 text-cyan-700"
              }`}
            >
              <Wallet className="h-5 w-5" />
            </span>
            <p className="text-sm font-semibold text-slate-600">DeepSeek 账户余额</p>
            {available ? (
              <span
                className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
                  alert
                    ? "border-rose-200 bg-rose-50 text-rose-700"
                    : "border-cyan-200 bg-cyan-50 text-cyan-700"
                }`}
              >
                {low ? "偏低" : "正常"}
              </span>
            ) : null}
          </div>
          <div
            className={`tabular mt-2 text-3xl font-black tracking-tight ${
              alert ? "text-rose-700" : "text-slate-950"
            }`}
          >
            {available && value !== null ? `¥${value.toFixed(2)}` : "余额暂不可用"}
          </div>
          <p className="mt-2 max-w-[72ch] text-sm leading-6 text-slate-600">
            {!available
              ? "暂时无法获取余额，请稍后再看或检查 API Key 配置。"
              : low
                ? `已低于告警阈值 ¥${threshold.toFixed(2)}，请尽快充值，以免影响 AI 回复与文档生成。`
                : `余额低于告警阈值 ¥${threshold.toFixed(2)} 时，会自动向指定群推送提醒（每天最多一条）。`}
          </p>
        </div>
      </div>
    </section>
  );
}

function CostsSection({
  data,
  balance,
  onSelectScenario,
  onSelectCase,
}: {
  data: AgentOperationsData;
  balance?: DeepseekBalance | null;
  onSelectScenario: (sc: ScenarioCost) => void;
  onSelectCase: (cc: CaseCost) => void;
}) {
  const averageCost = data.costs.calls
    ? data.costs.estimated_cost_yuan / data.costs.calls
    : 0;

  return (
    <>
      <AccountBalancePanel balance={balance} />
      <section className="panel overflow-hidden">
        <div className="grid gap-5 px-5 py-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
          <div>
            <p className="text-sm font-semibold text-slate-600">这段时间 AI 一共花了</p>
            <div className="tabular mt-1 text-3xl font-black tracking-tight text-slate-950">
              {formatCost(data.costs.estimated_cost_yuan)}
            </div>
            <p className="mt-2 max-w-[72ch] text-sm leading-6 text-slate-600">
              这里只算 AI 大模型的费用，不含服务器、企微通道等其它开销。点下面的功能或用户可以看得更细。
            </p>
          </div>
          <div className="grid grid-cols-2 gap-x-8 gap-y-3 border-t border-slate-100 pt-4 sm:grid-cols-3 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
            <PlainMetric label="调用次数" value={`${formatNumber(data.costs.calls)} 次`} />
            <PlainMetric label="平均每次" value={formatCost(averageCost)} />
            <PlainMetric label="涉及用户" value={`${formatNumber(data.costs.service_case_count)} 人`} />
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
  const cols =
    items.length >= 5 ? "xl:grid-cols-5" : "xl:grid-cols-4";
  return (
    <section className="panel overflow-hidden" aria-label="关键指标">
      <div className={`grid gap-px bg-slate-100 sm:grid-cols-2 ${cols}`}>
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
                <div className="text-xs font-bold text-slate-500">
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

function OutcomeBuckets({
  title,
  description,
  buckets,
  total,
  onSelectReason,
}: {
  title: string;
  description: string;
  buckets: Bucket[];
  total: number;
  onSelectReason: (key: string, total: number) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const visible = buckets.filter((b) => b.count > 0);

  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">{title}</h2>
        <p className="panel-subtitle">{description}</p>
      </div>
      {visible.length ? (
        <div className="divide-y divide-slate-100">
          {visible.map((bucket) => {
            const Icon = bucket.icon;
            const styles = TONE_STYLES[bucket.tone];
            const pct = total ? Math.round((bucket.count / total) * 100) : 0;
            const isOpen = expanded === bucket.id;
            const canExpand = bucket.reasons.length > 1;
            return (
              <div key={bucket.id}>
                <button
                  type="button"
                  onClick={() => setExpanded(isOpen ? null : bucket.id)}
                  className="flex w-full items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-slate-50/70 sm:px-6"
                >
                  <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl ${styles.icon}`}>
                    <Icon className="h-5 w-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-slate-900">{bucket.label}</span>
                      <ChevronDown
                        className={`h-4 w-4 text-slate-400 transition-transform ${isOpen ? "rotate-180" : ""}`}
                      />
                    </div>
                    <p className="mt-0.5 truncate text-xs text-slate-500">{bucket.hint}</p>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className={`h-full rounded-full ${styles.bar}`}
                        style={{ width: `${Math.max(2, pct)}%` }}
                      />
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="tabular text-lg font-black text-slate-900">{formatNumber(bucket.count)}</div>
                    <div className="text-xs font-semibold text-slate-400">{total ? `${pct}%` : "—"}</div>
                  </div>
                </button>

                {isOpen && (
                  <div className="bg-slate-50/70 px-5 pb-4 sm:px-6">
                    <div className="rounded-xl border border-slate-200 bg-white divide-y divide-slate-100">
                      {bucket.reasons.map((reason) => (
                        <button
                          key={reason.key}
                          type="button"
                          onClick={() => onSelectReason(reason.key, total)}
                          className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left transition-colors hover:bg-cyan-50/60"
                        >
                          <span className="min-w-0 truncate text-xs text-slate-700">
                            {OUTCOME_LABELS[reason.key] ?? reason.key}
                          </span>
                          <span className="flex shrink-0 items-center gap-1.5">
                            <span className="tabular text-xs font-bold text-slate-900">{reason.count}</span>
                            <ChevronRight className="h-3.5 w-3.5 text-slate-300" />
                          </span>
                        </button>
                      ))}
                    </div>
                    {canExpand && (
                      <p className="mt-2 text-[11px] text-slate-400">点具体原因可以看这是什么情况、要不要处理。</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-500">
          这段时间还没有相关处理记录。
        </div>
      )}
    </section>
  );
}

function OutputPanel({ archivist }: { archivist: ArchivistSummary }) {
  const entries = Object.entries(archivist.output_breakdown).sort(
    (a, b) => b[1] - a[1],
  );
  const max = Math.max(...entries.map(([, value]) => value), 1);
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">整理出了哪些信息</h2>
        <p className="panel-subtitle">
          这段时间从群聊里整理并存进档案的各类信息条数。
        </p>
      </div>
      {entries.length ? (
        <div className="divide-y divide-slate-100">
          {entries.map(([key, value]) => (
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
          这段时间还没有整理出新的档案信息。
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
        <h2 className="panel-title">问答 / 案例草稿</h2>
        <p className="panel-subtitle">AI 提议的新问答和案例，需要人工确认后才会正式启用。</p>
      </div>
      <div className="divide-y divide-slate-100">
        <InlineMetric label="草稿总数" value={total} />
        <InlineMetric label="待审核" value={statuses.pending ?? 0} />
        <InlineMetric label="已通过" value={statuses.approved ?? 0} />
        <InlineMetric label="已拒绝" value={statuses.rejected ?? 0} />
      </div>
      <div className="border-t border-slate-100 px-5 py-4 sm:px-6">
        <Link href="/documents" className="admin-link text-xs flex items-center gap-1 font-bold">
          去审核 <ArrowRight className="h-3.5 w-3.5" />
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
      <div className="panel-header-clean">
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-cyan-700" />
          <h2 className="panel-title">AI 用到的小工具</h2>
        </div>
        <p className="panel-subtitle">
          回复时 AI 会调用这些工具去查进度、查打卡、查资料等。点任意一行看用得顺不顺。
        </p>
      </div>

      {tools.length ? (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>工具</th>
                <th>用了多少次</th>
                <th>成功率</th>
                <th>平均耗时</th>
                <th>出错情况</th>
                <th className="w-10" aria-label="查看" />
              </tr>
            </thead>
            <tbody>
              {tools.map((tool) => {
                const errorCount = tool.failed_calls + tool.rejected_calls;
                return (
                  <tr
                    key={tool.name}
                    onClick={() => onSelectTool(tool)}
                    className="cursor-pointer group hover:bg-cyan-50/60"
                  >
                    <td>
                      <div className="font-semibold text-slate-900 group-hover:text-cyan-900">
                        {TOOL_LABELS[tool.name] ?? tool.name}
                      </div>
                    </td>
                    <td className="tabular font-semibold text-slate-800">{tool.calls} 次</td>
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
                    <td className="text-xs text-slate-600">{formatLatency(tool.average_latency_ms)}</td>
                    <td className="text-xs">
                      {errorCount > 0 ? (
                        <span className="text-rose-600 font-medium">{errorCount} 次没成功</span>
                      ) : (
                        <span className="text-slate-400">无</span>
                      )}
                    </td>
                    <td className="text-right">
                      <ChevronRight className="h-4 w-4 text-slate-300 group-hover:text-cyan-600 transition-colors" />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-500">
          这段时间 AI 没有用到工具。
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
        <h2 className="panel-title">各项功能分别花了多少</h2>
        <p className="panel-subtitle">点任意一项可以看它的花费明细。</p>
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
                  <div className="max-w-[60ch]">
                    <div className="font-semibold text-slate-900 group-hover:text-cyan-900 flex items-center gap-1.5">
                      {SCENARIO_LABELS[item.scenario] ?? item.scenario}
                      <ChevronRight className="h-4 w-4 text-slate-300 group-hover:text-cyan-600" />
                    </div>
                  </div>
                  <SmallDatum label="调用次数" value={`${formatNumber(item.calls)} 次`} />
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
          这段时间没有花费记录。
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
        <h2 className="panel-title">各个用户分别花了多少</h2>
        <p className="panel-subtitle">点任意一个用户可以看 TA 的花费明细。</p>
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
                  <div className="font-semibold text-slate-950 group-hover:text-cyan-900">
                    {item.user_name || "未命名用户"}
                  </div>
                  <div className="mt-0.5 text-xs text-slate-500">{item.case_no}</div>
                </div>
                <SmallDatum label="调用次数" value={`${formatNumber(item.calls)} 次`} />
                <SmallDatum label="合计花费" value={formatCost(item.estimated_cost_yuan)} />
                <SmallDatum
                  label="最近一次"
                  value={formatChinaShortDateTime(item.last_call_at)}
                />
                <ChevronRight className="h-5 w-5 text-slate-300 group-hover:text-cyan-600" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-600">
          这段时间没有关联到用户的花费记录。
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
        <h2 className="panel-title">每次整理记录</h2>
        <p className="panel-subtitle">点任意一次可以看这次读了哪些聊天、整理出了什么。</p>
      </div>
      {batches.length ? (
        <div className="divide-y divide-slate-100">
          {batches.map((batch) => (
            <div
              key={batch.id}
              onClick={() => onSelectBatch(batch)}
              className="px-5 py-4 sm:px-6 cursor-pointer hover:bg-cyan-50/50 transition-colors group flex items-center justify-between"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <StatusDot status={batch.status} />
                  <span className="font-semibold text-slate-900 group-hover:text-cyan-900 truncate">
                    {batch.user_name || batch.case_no || "未知用户"}
                  </span>
                  <span className="text-xs text-slate-400">
                    {formatChinaShortDateTime(batch.started_at)}
                  </span>
                </div>
                <div className="mt-1 text-xs text-slate-600">
                  读了 {batch.input_messages?.length || batch.input_count || 0} 条聊天 → 整理出{" "}
                  {batch.changes?.length || batch.output_count || 0} 项信息
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="text-xs font-semibold text-slate-800">
                  {formatCost(batch.estimated_cost_yuan)}
                </span>
                <ChevronRight className="h-4 w-4 text-slate-300 group-hover:text-cyan-600" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-600">
          这段时间还没有整理记录。
        </div>
      )}
    </section>
  );
}

const SCENARIO_LABELS: Record<string, string> = {
  reply_agent: "群内回复",
  agent_reminder: "定时提醒",
  archivist_extract: "整理档案",
  qwen_vl_translate: "看图识别",
  intervention_risk: "风险识别",
  intake_doc: "入营建档与文档",
};

function AgentTypeBadge({ type }: { type: string }) {
  const isArchivist = type === "archivist";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold ${
        isArchivist
          ? "bg-amber-50 text-amber-800 border border-amber-200"
          : "bg-cyan-50 text-cyan-800 border border-cyan-200"
      }`}
    >
      {isArchivist ? "整理档案" : "群内回复"}
    </span>
  );
}

function StatusDot({ status }: { status: string }) {
  const tone =
    status === "completed"
      ? "bg-emerald-500"
      : status === "failed"
      ? "bg-rose-500"
      : "bg-amber-500";
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${tone}`} aria-hidden="true" />;
}

function InlineMetric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="flex items-center justify-between gap-4 px-5 py-4 sm:block sm:px-6">
      <div className="text-sm font-semibold text-slate-600">{label}</div>
      <div className="tabular mt-1 text-xl font-black text-slate-950">{value}</div>
    </div>
  );
}

function PlainMetric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-semibold text-slate-500">{label}</div>
      <div className="tabular mt-1 text-lg font-black text-slate-900">{value}</div>
      {detail && <div className="mt-0.5 text-xs text-slate-400">{detail}</div>}
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

function reasonCount(summary: RuntimeSummary, key: string) {
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
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} 毫秒`;
}

function percentDetail(value: number, total: number, prefix: string) {
  return total ? `${prefix} ${((value / total) * 100).toFixed(1)}%` : "暂无数据";
}
