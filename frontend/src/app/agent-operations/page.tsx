import Link from "next/link";
import {
  Activity,
  Archive,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  Coins,
  MessageSquareReply,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";

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
type ToolStat = {
  name: string;
  calls: number;
  successful_calls: number;
  failed_calls: number;
  rejected_calls: number;
  success_rate?: number | null;
  average_latency_ms?: number | null;
  errors: OutcomeCount[];
};
type ScenarioCost = {
  scenario: string;
  calls: number;
  successful_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_yuan: number;
};
type CaseCost = {
  service_case_id: number;
  case_no: string;
  user_name?: string | null;
  calls: number;
  total_tokens: number;
  estimated_cost_yuan: number;
  last_call_at?: string | null;
};
type RecentRun = {
  id: number;
  agent_type: string;
  run_kind: string;
  status: string;
  outcome?: string | null;
  service_case_id?: number | null;
  case_no?: string | null;
  user_name?: string | null;
  model_calls: number;
  tool_calls: number;
  total_tokens: number;
  estimated_cost_yuan: number;
  input_count: number;
  output_count: number;
  latency_ms?: number | null;
  error_type?: string | null;
  started_at: string;
  finished_at?: string | null;
};
type AgentOperationsData = {
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
    scenarios: ScenarioCost[];
    cases: CaseCost[];
  };
  recent_runs: RecentRun[];
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

const SCENARIO_LABELS: Record<string, string> = {
  reply_agent: "回复 Agent",
  agent_reminder: "Agent 定时提醒",
  archivist_extract: "档案官",
  qwen_vl_translate: "图片理解",
  intervention_analysis: "风控与意图识别",
  document_generation: "文档生成",
  outcome_snapshot: "成效快照",
  knowledge_evolution: "知识进化",
};

export default async function AgentOperationsPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string; days?: string }>;
}) {
  const params = await searchParams;
  const tab = TABS.some((item) => item.key === params.tab)
    ? (params.tab as TabKey)
    : "overview";
  const parsedDays = Number(params.days);
  const days = [7, 30, 90].includes(parsedDays) ? parsedDays : 30;
  const data = await api<AgentOperationsData>(
    `/agent-operations?days=${days}`,
  ).catch(() => null);

  return (
    <div className="page-stack">
      <header className="page-header">
        <div className="page-header-row">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-cyan-50 text-cyan-700">
              <Activity className="h-5 w-5" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <h1 className="page-heading">AI 运行</h1>
              <p className="page-subtitle">
                看清当前回复 Agent 与档案官实际做了什么、结果如何，以及每一笔模型成本花在哪里。
              </p>
            </div>
          </div>
          {data && (
            <span className="status-pill pill-neutral shrink-0 self-start">
              统计至 {formatChinaShortDateTime(data.generated_at)}
            </span>
          )}
        </div>

        <div className="mt-5 flex min-w-0 flex-col gap-3 border-t border-slate-100 pt-4 lg:flex-row lg:items-center lg:justify-between">
          <nav
            aria-label="AI 运行分类"
            className="grid grid-cols-2 gap-1 rounded-xl border border-slate-200 bg-slate-100/80 p-1 sm:inline-grid sm:grid-cols-4"
          >
            {TABS.map((item) => {
              const Icon = item.icon;
              const active = tab === item.key;
              return (
                <Link
                  key={item.key}
                  href={buildHref(item.key, days)}
                  aria-current={active ? "page" : undefined}
                  className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-sm font-semibold transition-colors ${
                    active
                      ? "bg-white text-cyan-800 shadow-sm"
                      : "text-slate-600 hover:bg-white/60 hover:text-slate-950"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1">
            {[7, 30, 90].map((value) => (
              <Link
                key={value}
                href={buildHref(tab, value)}
                className={`inline-flex min-h-11 items-center justify-center rounded-lg px-3 py-2 text-center text-xs font-bold transition-colors ${
                  days === value
                    ? "bg-slate-900 text-white"
                    : "text-slate-500 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                {value} 天
              </Link>
            ))}
          </div>
        </div>
      </header>

      {!data ? (
        <div className="empty-state">
          <TriangleAlert className="h-6 w-6 text-amber-500" />
          AI 运行统计暂时无法读取，请稍后刷新。
        </div>
      ) : (
        <>
          {tab === "overview" && <OverviewTab data={data} />}
          {tab === "reply" && <ReplyTab data={data} />}
          {tab === "archivist" && <ArchivistTab data={data} />}
          {tab === "costs" && <CostsTab data={data} />}
        </>
      )}
    </div>
  );
}

function OverviewTab({ data }: { data: AgentOperationsData }) {
  return (
    <>
      <MetricStrip
        items={[
          {
            label: "Agent 运行",
            value: formatNumber(data.all_agents.runs),
            detail: `${data.all_agents.running_runs} 次仍在运行`,
            icon: Activity,
          },
          {
            label: "模型调用",
            value: formatNumber(data.all_agents.model_calls),
            detail: `${formatNumber(data.all_agents.total_tokens)} Tokens`,
            icon: MessageSquareReply,
          },
          {
            label: "Agent 成本",
            value: formatCost(data.all_agents.estimated_cost_yuan),
            detail: "仅回复、提醒与档案官",
            icon: CircleDollarSign,
          },
          {
            label: "失败运行",
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
          description="每个对话轮次只计一次；主动不回复、转人工和发送失败分别展示。"
          outcomes={data.reply.outcomes}
          total={data.reply.runs}
          empty="所选时间内还没有回复 Agent 运行记录。"
        />
        <OutputPanel archivist={data.archivist} />
      </div>

      <RecentRuns runs={data.recent_runs} />
    </>
  );
}

function ReplyTab({ data }: { data: AgentOperationsData }) {
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
            label: "对话轮次",
            value: formatNumber(data.reply.runs),
            detail: "按聚合后的用户 turn 计",
            icon: Bot,
          },
          {
            label: "已发送",
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
            label: "转人工",
            value: formatNumber(handoff),
            detail: percentDetail(handoff, data.reply.runs),
            icon: TriangleAlert,
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
        description="这里保留每种业务原因，不用一个笼统回复率掩盖“为什么没有发”。"
        outcomes={data.reply.outcomes}
        total={data.reply.runs}
        empty="所选时间内还没有回复 Agent 运行记录。"
      />

      <ToolPanel tools={data.tools} />

      <section className="panel overflow-hidden">
        <div className="panel-header-clean">
          <h2 className="panel-title">Agent 定时提醒</h2>
          <p className="panel-subtitle">
            与群聊回复使用同一套 ReAct Agent，但不进入对话回复率分母。
          </p>
        </div>
        <div className="grid divide-y divide-slate-100 sm:grid-cols-4 sm:divide-x sm:divide-y-0">
          <InlineMetric label="运行" value={data.reminders.runs} />
          <InlineMetric
            label="已发送"
            value={outcomeCount(data.reminders, "reminder_sent")}
          />
          <InlineMetric label="失败" value={data.reminders.failed_runs} />
          <InlineMetric
            label="估算成本"
            value={formatCost(data.reminders.estimated_cost_yuan)}
          />
        </div>
      </section>
    </>
  );
}

function ArchivistTab({ data }: { data: AgentOperationsData }) {
  const useful = outcomeCount(data.archivist, "archived_with_output");
  const noOutput = outcomeCount(data.archivist, "archived_no_output");
  return (
    <>
      <MetricStrip
        items={[
          {
            label: "归档批次",
            value: formatNumber(data.archivist.runs),
            detail: `${formatNumber(data.archivist.input_count)} 条增量消息`,
            icon: Archive,
          },
          {
            label: "有实际产出",
            value: formatNumber(useful),
            detail: percentDetail(useful, data.archivist.runs),
            icon: CheckCircle2,
            tone: "ok",
          },
          {
            label: "成功但无产出",
            value: formatNumber(noOutput),
            detail: percentDetail(noOutput, data.archivist.runs),
            icon: MessageSquareReply,
          },
          {
            label: "校验丢弃",
            value: formatNumber(data.archivist.dropped_count),
            detail: "模型上报后未通过确定性校验",
            icon: TriangleAlert,
            tone: data.archivist.dropped_count ? "warn" : "neutral",
          },
          {
            label: "归档耗时 P95",
            value: formatLatency(data.archivist.latency_p95_ms),
            detail: `P50 ${formatLatency(data.archivist.latency_p50_ms)}`,
            icon: Clock3,
          },
        ]}
      />

      <div className="grid gap-5 xl:grid-cols-[1.25fr_0.75fr]">
        <OutputPanel archivist={data.archivist} expanded />
        <DraftReviewPanel statuses={data.archivist.draft_statuses} />
      </div>

      <OutcomePanel
        title="归档运行终态"
        description="无产出是一次正常完成的归档判断；失败则表示事务回滚，水位线未推进、等待重试。"
        outcomes={data.archivist.outcomes}
        total={data.archivist.runs}
        empty="所选时间内还没有档案官运行记录。"
      />
    </>
  );
}

function CostsTab({ data }: { data: AgentOperationsData }) {
  const successRate = data.costs.calls
    ? (data.costs.successful_calls / data.costs.calls) * 100
    : null;
  return (
    <>
      <MetricStrip
        items={[
          {
            label: "全部 AI 估算成本",
            value: formatCost(data.costs.estimated_cost_yuan),
            detail: "包含 Agent、识图、风控、文档等",
            icon: Coins,
          },
          {
            label: "模型调用",
            value: formatNumber(data.costs.calls),
            detail:
              successRate === null
                ? "暂无调用"
                : `${successRate.toFixed(1)}% 调用成功`,
            icon: Activity,
          },
          {
            label: "总 Tokens",
            value: formatNumber(data.costs.total_tokens),
            detail: `输入 ${formatNumber(data.costs.prompt_tokens)} · 输出 ${formatNumber(data.costs.completion_tokens)}`,
            icon: MessageSquareReply,
          },
          {
            label: "Agent 成本占比",
            value: percentValue(
              data.all_agents.estimated_cost_yuan,
              data.costs.estimated_cost_yuan,
            ),
            detail: formatCost(data.all_agents.estimated_cost_yuan),
            icon: CircleDollarSign,
          },
        ]}
      />

      <ScenarioCosts scenarios={data.costs.scenarios} />
      <CaseCosts cases={data.costs.cases} />
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
      <div
        className={`grid gap-px bg-slate-100 sm:grid-cols-2 ${
          items.length === 5 ? "xl:grid-cols-5" : "xl:grid-cols-4"
        }`}
      >
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
            <div key={item.label} className="flex min-w-0 items-start gap-3 bg-white p-4 sm:p-5">
              <span
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${iconTone}`}
              >
                <Icon className="h-[18px] w-[18px]" />
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
}: {
  title: string;
  description: string;
  outcomes: OutcomeCount[];
  total: number;
  empty: string;
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
              className="grid min-w-0 gap-2 px-5 py-3.5 sm:grid-cols-[minmax(0,14rem)_minmax(8rem,1fr)_5rem] sm:items-center sm:px-6"
            >
              <div className="min-w-0">
                <div className="text-sm font-semibold text-slate-800">
                  {OUTCOME_LABELS[item.key] ?? item.key}
                </div>
                <div className="mt-0.5 text-xs text-slate-400">{item.key}</div>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full bg-cyan-500"
                  style={{ width: `${Math.max(3, (item.count / max) * 100)}%` }}
                />
              </div>
              <div className="tabular text-right text-sm font-bold text-slate-900">
                {item.count}
                <span className="ml-1 text-xs font-medium text-slate-400">
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
        <h2 className="panel-title">档案官的确定性产出</h2>
        <p className="panel-subtitle">
          模型先结构化上报，只有通过校验并成功写库的内容才计入这里。
        </p>
      </div>
      {entries.length ? (
        <div className="divide-y divide-slate-100">
          {entries.slice(0, expanded ? entries.length : 6).map(([key, value]) => (
            <div key={key} className="grid grid-cols-[minmax(0,1fr)_4rem] items-center gap-4 px-5 py-3 sm:px-6">
              <div className="min-w-0">
                <div className="flex items-center justify-between gap-3 text-sm">
                  <span className="font-semibold text-slate-700">
                    {OUTPUT_LABELS[key] ?? key}
                  </span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-emerald-500"
                    style={{ width: `${Math.max(3, (value / max) * 100)}%` }}
                  />
                </div>
              </div>
              <div className="tabular text-right text-lg font-black text-slate-900">
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
        <p className="panel-subtitle">人工审核结果是衡量草稿质量的直接反馈。</p>
      </div>
      <div className="divide-y divide-slate-100">
        <InlineMetric label="草稿总数" value={total} />
        <InlineMetric label="待审核" value={statuses.pending ?? 0} />
        <InlineMetric label="已通过" value={statuses.approved ?? 0} />
        <InlineMetric label="已拒绝" value={statuses.rejected ?? 0} />
      </div>
      <div className="border-t border-slate-100 px-5 py-4 sm:px-6">
        <Link href="/documents" className="admin-link text-sm">
          前往审核控制台
        </Link>
      </div>
    </section>
  );
}

function ToolPanel({ tools }: { tools: ToolStat[] }) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-cyan-700" />
          <h2 className="panel-title">工具调用表现</h2>
        </div>
        <p className="panel-subtitle">
          拒绝表示触发调用上限、熔断或策略边界；失败表示工具实际执行未成功。
        </p>
      </div>
      {tools.length ? (
        <>
          <div className="mobile-card-list !grid gap-0 divide-y divide-slate-100 md:!hidden">
            {tools.map((tool) => (
              <div key={tool.name} className="px-5 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-semibold text-slate-900">
                      {TOOL_LABELS[tool.name] ?? tool.name}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-400">{tool.name}</div>
                  </div>
                  <span className="status-pill pill-neutral">{tool.calls} 次</span>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                  <SmallDatum label="成功率" value={tool.success_rate == null ? "—" : `${tool.success_rate}%`} />
                  <SmallDatum label="失败 / 拒绝" value={`${tool.failed_calls} / ${tool.rejected_calls}`} />
                  <SmallDatum label="平均耗时" value={formatLatency(tool.average_latency_ms)} />
                </div>
                {tool.errors.length > 0 && (
                  <p className="mt-3 text-xs text-rose-600">
                    {tool.errors.map((item) => `${item.key} × ${item.count}`).join(" · ")}
                  </p>
                )}
              </div>
            ))}
          </div>
          <div className="hidden md:block">
            <table className="data-table">
              <thead>
                <tr>
                  <th>工具</th>
                  <th>调用</th>
                  <th>成功率</th>
                  <th>失败 / 拒绝</th>
                  <th>平均耗时</th>
                  <th>主要错误</th>
                </tr>
              </thead>
              <tbody>
                {tools.map((tool) => (
                  <tr key={tool.name}>
                    <td>
                      <div className="font-semibold text-slate-900">
                        {TOOL_LABELS[tool.name] ?? tool.name}
                      </div>
                      <div className="mt-0.5 text-xs text-slate-400">{tool.name}</div>
                    </td>
                    <td className="tabular font-semibold">{tool.calls}</td>
                    <td className="tabular">
                      {tool.success_rate == null ? "—" : `${tool.success_rate}%`}
                    </td>
                    <td className="tabular">
                      {tool.failed_calls} / {tool.rejected_calls}
                    </td>
                    <td>{formatLatency(tool.average_latency_ms)}</td>
                    <td className="max-w-[18rem] text-xs text-rose-600">
                      {tool.errors.length
                        ? tool.errors
                            .map((item) => `${item.key} × ${item.count}`)
                            .join(" · ")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-500">
          所选时间内没有工具调用。
        </div>
      )}
    </section>
  );
}

function ScenarioCosts({ scenarios }: { scenarios: ScenarioCost[] }) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">按调用场景</h2>
        <p className="panel-subtitle">
          先看是哪类任务花的钱，再决定优化提示词、调用频次还是模型配置。
        </p>
      </div>
      {scenarios.length ? (
        <div className="divide-y divide-slate-100">
          {scenarios.map((item) => (
            <div
              key={item.scenario}
              className="grid gap-3 px-5 py-4 sm:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(6rem,0.6fr))] sm:items-center sm:px-6"
            >
              <div>
                <div className="font-semibold text-slate-900">
                  {SCENARIO_LABELS[item.scenario] ?? item.scenario}
                </div>
                <div className="mt-0.5 text-xs text-slate-400">{item.scenario}</div>
              </div>
              <SmallDatum label="调用" value={formatNumber(item.calls)} />
              <SmallDatum label="Tokens" value={formatNumber(item.total_tokens)} />
              <SmallDatum label="估算成本" value={formatCost(item.estimated_cost_yuan)} />
            </div>
          ))}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-500">
          所选时间内没有 AI 成本记录。
        </div>
      )}
    </section>
  );
}

function CaseCosts({ cases }: { cases: CaseCost[] }) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">按服务单</h2>
        <p className="panel-subtitle">定位高消耗用户，结合调用场景判断是否合理。</p>
      </div>
      {cases.length ? (
        <>
          <div className="mobile-card-list !grid gap-0 divide-y divide-slate-100 md:!hidden">
            {cases.map((item) => (
              <Link
                key={item.service_case_id}
                href={`/cases/${item.service_case_id}`}
                className="px-5 py-4 transition-colors hover:bg-cyan-50/40"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-semibold text-slate-900">
                      {item.user_name || "未命名用户"}
                    </div>
                    <div className="mt-0.5 text-xs text-cyan-700">{item.case_no}</div>
                  </div>
                  <span className="tabular font-black text-slate-950">
                    {formatCost(item.estimated_cost_yuan)}
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                  <SmallDatum label="调用" value={formatNumber(item.calls)} />
                  <SmallDatum label="Tokens" value={formatNumber(item.total_tokens)} />
                  <SmallDatum label="最后调用" value={formatChinaShortDateTime(item.last_call_at)} />
                </div>
              </Link>
            ))}
          </div>
          <div className="hidden md:block">
            <table className="data-table">
              <thead>
                <tr>
                  <th>用户 / 服务单</th>
                  <th>调用</th>
                  <th>Tokens</th>
                  <th>估算成本</th>
                  <th>最后调用</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((item) => (
                  <tr key={item.service_case_id}>
                    <td>
                      <div className="font-semibold text-slate-900">
                        {item.user_name || "未命名用户"}
                      </div>
                      <Link
                        href={`/cases/${item.service_case_id}`}
                        className="admin-link mt-0.5 inline-flex text-xs"
                      >
                        {item.case_no}
                      </Link>
                    </td>
                    <td className="tabular">{formatNumber(item.calls)}</td>
                    <td className="tabular">{formatNumber(item.total_tokens)}</td>
                    <td className="tabular font-bold text-slate-950">
                      {formatCost(item.estimated_cost_yuan)}
                    </td>
                    <td>{formatChinaShortDateTime(item.last_call_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-500">
          所选时间内没有可关联到服务单的成本记录。
        </div>
      )}
    </section>
  );
}

function RecentRuns({ runs }: { runs: RecentRun[] }) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">最近运行</h2>
        <p className="panel-subtitle">用于快速发现连续失败、长期运行中或异常高耗时。</p>
      </div>
      {runs.length ? (
        <>
          <div className="mobile-card-list !grid gap-0 divide-y divide-slate-100 md:!hidden">
            {runs.map((run) => (
              <div key={run.id} className="px-5 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-semibold text-slate-900">
                      {run.agent_type === "archivist" ? "档案官" : "回复 Agent"}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">
                      {run.user_name || run.case_no || "系统任务"} ·{" "}
                      {formatChinaShortDateTime(run.started_at)}
                    </div>
                  </div>
                  <RunStatus run={run} />
                </div>
                <div className="mt-3 text-sm text-slate-700">
                  {OUTCOME_LABELS[run.outcome ?? "running"] ?? run.outcome ?? "运行中"}
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                  <SmallDatum label="模型 / 工具" value={`${run.model_calls} / ${run.tool_calls}`} />
                  <SmallDatum label="Tokens" value={formatNumber(run.total_tokens)} />
                  <SmallDatum label="耗时" value={formatLatency(run.latency_ms)} />
                </div>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
            <table className="data-table">
              <thead>
                <tr>
                  <th>时间 / 对象</th>
                  <th>Agent</th>
                  <th>终态</th>
                  <th>模型 / 工具</th>
                  <th>Tokens</th>
                  <th>耗时</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <div className="text-sm text-slate-700">
                        {formatChinaShortDateTime(run.started_at)}
                      </div>
                      {run.service_case_id ? (
                        <Link
                          href={`/cases/${run.service_case_id}`}
                          className="admin-link mt-0.5 inline-flex text-xs"
                        >
                          {run.user_name || run.case_no || `服务单 ${run.service_case_id}`}
                        </Link>
                      ) : (
                        <div className="mt-0.5 text-xs text-slate-400">系统任务</div>
                      )}
                    </td>
                    <td>
                      <div className="font-semibold text-slate-900">
                        {run.agent_type === "archivist" ? "档案官" : "回复 Agent"}
                      </div>
                      <div className="mt-0.5 text-xs text-slate-400">{run.run_kind}</div>
                    </td>
                    <td>
                      <RunStatus run={run} />
                      <div className="mt-1 text-xs text-slate-500">
                        {OUTCOME_LABELS[run.outcome ?? "running"] ?? run.outcome ?? "运行中"}
                      </div>
                    </td>
                    <td className="tabular">{run.model_calls} / {run.tool_calls}</td>
                    <td className="tabular">{formatNumber(run.total_tokens)}</td>
                    <td>{formatLatency(run.latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-500">
          所选时间内还没有 Agent 运行记录。
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
      <div className="text-xs font-bold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="tabular mt-1 text-xl font-black text-slate-950">{value}</div>
    </div>
  );
}

function SmallDatum({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[0.68rem] font-bold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-break tabular mt-1 font-semibold text-slate-700">{value}</div>
    </div>
  );
}

function buildHref(tab: TabKey, days: number) {
  return `/agent-operations?tab=${tab}&days=${days}`;
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
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} ms`;
}

function failureDescription(summary: RuntimeSummary) {
  if (!summary.runs) return "暂无运行记录";
  if (!summary.failed_runs) return "所选时间内无失败";
  return `${((summary.failed_runs / summary.runs) * 100).toFixed(1)}% 的运行失败`;
}

function percentDetail(value: number, total: number) {
  return total ? `占全部轮次 ${((value / total) * 100).toFixed(1)}%` : "暂无样本";
}

function percentValue(value: number, total: number) {
  return total > 0 ? `${((value / total) * 100).toFixed(1)}%` : "—";
}
