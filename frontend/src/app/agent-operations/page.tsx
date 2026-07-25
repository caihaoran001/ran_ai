import Link from "next/link";
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
  scenarios: ScenarioCost[];
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
type ArchivistInputMessage = {
  id: number;
  role: string;
  content: string;
  created_at: string;
  is_context: boolean;
};
type ArchivistBatchChange = {
  kind: string;
  action: string;
  title: string;
  before?: string | null;
  after?: string | null;
  evidence?: string | null;
  items: Array<{
    field?: string;
    label?: string;
    value?: string | null;
    before?: string | null;
    after?: string | null;
  }>;
};
type ArchivistBatch = RecentRun & {
  trigger_reason?: string | null;
  input_source: "exact" | "watermark_range" | "unavailable";
  input_messages: ArchivistInputMessage[];
  changes: ArchivistBatchChange[];
  changes_detail_available: boolean;
  dropped_items: Array<{
    bucket?: string;
    item_id?: string;
    reason?: string;
  }>;
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

const SCENARIO_META: Record<string, { label: string; description: string }> = {
  reply_agent: {
    label: "群内回复",
    description: "回复 Agent 阅读对话、判断是否回复以及调用工具后的模型思考。",
  },
  agent_reminder: {
    label: "定时提醒文案",
    description: "到点后由回复 Agent 重新组织提醒内容。",
  },
  archivist_extract: {
    label: "档案官归档",
    description: "读取一批新聊天并一次性抽取小结、成效、画像、FAQ 与案例素材。",
  },
  qwen_vl_translate: {
    label: "图片理解",
    description: "识别用户发来的图片内容，供回复与归档继续处理。",
  },
  intervention_risk: {
    label: "风险与意图判断",
    description: "判断是否存在风险、退款/续购意图或需要转人工。",
  },
  intervention_analysis: {
    label: "风险与意图判断",
    description: "判断是否存在风险、退款/续购意图或需要转人工。",
  },
  intake_doc: {
    label: "入营画像与文档",
    description: "根据入营问卷生成用户画像和交付内容。",
  },
  stage_report: {
    label: "阶段报告",
    description: "按服务阶段生成阶段复盘与交付报告。",
  },
  population_insight: {
    label: "人群洞察",
    description: "批量汇总用户特征，生成运营人群洞察。",
  },
  profile_refresh: {
    label: "用户画像刷新",
    description: "根据最新问卷或服务信息重新生成用户画像。",
  },
  checkin_followup: {
    label: "打卡追问",
    description: "根据当天缺失项生成个性化追问。",
  },
  checkin_extract: {
    label: "打卡信息抽取",
    description: "历史流程中的聊天打卡结构化抽取。",
  },
  generic: {
    label: "其他模型任务",
    description: "未单独归类的后台模型调用。",
  },
};

const CHANGE_KIND_LABELS: Record<string, string> = {
  checkin: "打卡记录",
  checkin_conflict: "打卡冲突",
  outcome: "阶段成效",
  profile_fact: "稳定画像",
  state_fact: "当前状态",
  personal_guidance: "个人指导",
  correction: "班主任纠错",
  faq_draft: "FAQ 草稿",
  user_summary: "用户历程小结",
  case_material: "优秀案例素材小结",
  case_draft: "优秀案例草稿",
  user_state: "用户档案",
  checkin_days: "打卡记录",
  outcomes: "阶段成效",
  profile_facts: "稳定画像",
  state_facts: "当前状态",
  personal_guidance_count: "个人指导",
  corrections: "班主任纠错",
  faq_drafts: "FAQ 草稿",
  memory_updates: "用户小结 / 案例素材",
  user_summary_updates: "用户历程小结",
  case_material_updates: "优秀案例素材小结",
  case_drafts: "优秀案例草稿",
};

const CHECKIN_FIELD_LABELS: Record<string, string> = {
  weight: "体重",
  waist: "腰围",
  sleep: "睡眠",
  water: "饮水",
  exercise: "运动",
  diet: "饮食",
  mood: "情绪",
  morning_c: "早 C",
  noon_b: "午 B",
  night_a: "晚 A",
  "product_schedule.morning_c": "早 C",
  "product_schedule.noon_b": "午 B",
  "product_schedule.night_a": "晚 A",
  "product_schedule.summary": "产品执行",
};

const TRIGGER_LABELS: Record<string, string> = {
  fast_key: "发现值得归档的信息，用户停说约 3 分钟后触发",
  B_latency: "未归档信息达到最长等待时间",
  C_count: "未归档消息累计达到批量阈值",
  manual_replay: "人工重新处理隔离消息",
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
      <section className="panel overflow-hidden">
        <div className="flex flex-col gap-5 px-5 py-5 sm:px-6">
          <div className="max-w-[75ch]">
            <h2 className="panel-title">这段时间，档案官处理了什么</h2>
            <p className="panel-subtitle">
              一批代表一次模型归档：读取一组新聊天，随后由确定性代码把通过校验的内容写入档案。
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

      <ArchivistBatchLedger batches={data.archivist_batches} />

      <div className="grid gap-5 xl:grid-cols-[1.25fr_0.75fr]">
        <OutputPanel archivist={data.archivist} expanded />
        <DraftReviewPanel statuses={data.archivist.draft_statuses} />
      </div>
    </>
  );
}

function CostsTab({ data }: { data: AgentOperationsData }) {
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
              只统计大模型请求，不含服务器、企微通道等费用。一次回复或归档可能包含多次模型请求。
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
      />
      <CaseCosts cases={data.costs.cases} />
    </>
  );
}

function PlainMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-sm text-slate-600">{label}</div>
      <div className="tabular mt-1 text-lg font-bold text-slate-950">{value}</div>
    </div>
  );
}

function ArchivistBatchLedger({ batches }: { batches: ArchivistBatch[] }) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">逐批查看：聊天输入 → 实际更新</h2>
        <p className="panel-subtitle">
          展开任一批次即可核对当时送给档案官的聊天，以及确定性写库最终留下的内容。
        </p>
      </div>
      {batches.length ? (
        <div className="divide-y divide-slate-100">
          {batches.map((batch) => {
            const inputCount =
              batch.input_messages.length || batch.input_count;
            const appliedChanges = batch.changes.filter(
              (item) => item.action !== "not_applied",
            );
            return (
              <details key={batch.id} className="group">
                <summary className="cursor-pointer list-none px-5 py-4 transition-colors hover:bg-cyan-50/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-600 sm:px-6 [&::-webkit-details-marker]:hidden">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <RunStatus run={batch} />
                        <span className="text-sm font-bold text-slate-950">
                          {batch.user_name || batch.case_no || "未知服务单"}
                        </span>
                        <span className="text-xs text-slate-500">
                          {formatChinaShortDateTime(batch.started_at)}
                        </span>
                      </div>
                      <p className="mt-1.5 text-sm text-slate-600">
                        {triggerDescription(batch.trigger_reason)}
                      </p>
                    </div>
                    <div className="flex items-center gap-3 sm:gap-5">
                      <div className="flex min-w-0 items-center gap-2 text-sm">
                        <span className="font-bold text-slate-950">
                          输入 {formatNumber(inputCount)} 条
                        </span>
                        <ArrowRight className="h-4 w-4 shrink-0 text-slate-400" />
                        <span className="font-bold text-emerald-700">
                          更新 {formatNumber(appliedChanges.length || batch.output_count)} 项
                        </span>
                      </div>
                      <span className="tabular text-sm font-semibold text-slate-700">
                        {formatCost(batch.estimated_cost_yuan)}
                      </span>
                      <ChevronDown className="h-5 w-5 shrink-0 text-slate-400 transition-transform duration-200 group-open:rotate-180" />
                    </div>
                  </div>
                </summary>

                <div className="border-t border-slate-100 bg-slate-50/70 px-5 py-5 sm:px-6">
                  {batch.input_source === "watermark_range" && (
                    <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-900">
                      这是详细留痕上线前的历史批次，输入聊天按当时的归档水位范围还原；实际写入只能确认类型和数量。
                    </p>
                  )}
                  <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_2rem_minmax(0,1fr)]">
                    <BatchMessages batch={batch} />
                    <div className="hidden items-start justify-center pt-9 lg:flex">
                      <ArrowRight className="h-5 w-5 text-slate-400" />
                    </div>
                    <BatchChanges batch={batch} />
                  </div>

                  <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-slate-200 pt-4 text-xs text-slate-600">
                    {batch.service_case_id && (
                      <Link
                        href={`/cases/${batch.service_case_id}`}
                        className="admin-link"
                      >
                        查看服务单
                      </Link>
                    )}
                    <span>模型请求 {batch.model_calls} 次</span>
                    <span>用量 {formatNumber(batch.total_tokens)} Tokens</span>
                    <span>耗时 {formatLatency(batch.latency_ms)}</span>
                    <span>估算成本 {formatCost(batch.estimated_cost_yuan)}</span>
                  </div>
                </div>
              </details>
            );
          })}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-600">
          所选时间内还没有档案官批次。新批次出现后，这里会展示输入聊天和实际更新。
        </div>
      )}
    </section>
  );
}

function BatchMessages({ batch }: { batch: ArchivistBatch }) {
  return (
    <div className="min-w-0">
      <h3 className="text-sm font-bold text-slate-950">本批聊天输入</h3>
      <p className="mt-1 text-xs leading-5 text-slate-600">
        按实际送入模型的顺序展示；“上下文”表示为理解班主任纠正而补入的助教消息。
      </p>
      {batch.input_messages.length ? (
        <div className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
          {batch.input_messages.map((message) => (
            <div key={message.id} className="py-3">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className={`status-pill ${messageRoleTone(message.role)}`}>
                  {message.role}
                </span>
                {message.is_context && (
                  <span className="status-pill pill-neutral">补充上下文</span>
                )}
                <span className="text-slate-500">
                  {formatChinaShortDateTime(message.created_at)}
                </span>
                <span className="text-slate-400">消息 #{message.id}</span>
              </div>
              <p className="text-break mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-800">
                {message.content}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-3 border-y border-dashed border-slate-300 py-6 text-sm text-slate-600">
          该批次没有可展示的聊天原文。失败批次可能在输入范围确定前就已终止。
        </div>
      )}
    </div>
  );
}

function BatchChanges({ batch }: { batch: ArchivistBatch }) {
  return (
    <div className="min-w-0">
      <h3 className="text-sm font-bold text-slate-950">最终写入的变化</h3>
      <p className="mt-1 text-xs leading-5 text-slate-600">
        只展示通过确定性校验后的结果；模型提议但未写入的内容不算更新。
      </p>
      {batch.changes.length ? (
        <div className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
          {batch.changes.map((change, index) => (
            <BatchChangeDetail
              key={`${change.kind}-${change.title}-${index}`}
              change={change}
              detailAvailable={batch.changes_detail_available}
            />
          ))}
        </div>
      ) : (
        <div className="mt-3 border-y border-dashed border-slate-300 py-6 text-sm leading-6 text-slate-600">
          {batch.status === "failed"
            ? `本批失败并回滚，没有写入档案。${batch.error_type ? `错误类型：${batch.error_type}` : ""}`
            : "本批聊天已检查，但没有形成新的可写入内容。"}
        </div>
      )}
      {batch.dropped_items.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-semibold text-amber-800">
            另有 {batch.dropped_items.length} 项模型提议未通过校验
          </summary>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-600">
            {batch.dropped_items.map((item, index) => (
              <li key={`${item.bucket}-${item.item_id}-${index}`}>
                {CHANGE_KIND_LABELS[item.bucket || ""] || item.bucket || "未知类型"}：
                {droppedReason(item.reason)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function BatchChangeDetail({
  change,
  detailAvailable,
}: {
  change: ArchivistBatchChange;
  detailAvailable: boolean;
}) {
  const notApplied = change.action === "not_applied";
  const kindLabel = CHANGE_KIND_LABELS[change.kind] || change.kind;
  return (
    <div className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`status-pill ${notApplied ? "pill-warn" : "pill-ok"}`}>
          {notApplied ? "未覆盖" : actionLabel(change.action)}
        </span>
        <span className="text-sm font-bold text-slate-900">
          {kindLabel || change.title}
        </span>
      </div>
      {change.title &&
        change.title !== change.kind &&
        change.title !== kindLabel && (
          <p className="mt-1 text-sm text-slate-700">{change.title}</p>
        )}
      {!detailAvailable && (
        <p className="mt-2 text-sm text-slate-600">
          {change.after}；该历史批次未保存更新前后的具体值。
        </p>
      )}
      {detailAvailable && change.items.length > 0 && (
        <dl className="mt-2 space-y-2">
          {change.items.map((item, index) => (
            <div
              key={`${item.field}-${index}`}
              className="grid gap-1 text-sm sm:grid-cols-[8rem_minmax(0,1fr)]"
            >
              <dt className="font-semibold text-slate-600">
                {item.label ||
                  CHECKIN_FIELD_LABELS[item.field || ""] ||
                  item.field ||
                  "更新项"}
              </dt>
              <dd className="text-break text-slate-900">
                {item.value || item.after || "—"}
                {item.before && (
                  <span className="mt-1 block text-xs text-slate-500">
                    原值：{item.before}
                  </span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {detailAvailable && (change.before || change.after) && (
        <div className="mt-2 grid gap-2 text-sm">
          {change.before && (
            <div className="rounded-lg bg-slate-200/70 px-3 py-2 text-slate-700">
              <span className="mr-2 font-semibold">更新前</span>
              <span className="text-break whitespace-pre-wrap">
                {formatAuditValue(change.before)}
              </span>
            </div>
          )}
          {change.after && (
            <div className="rounded-lg bg-emerald-50 px-3 py-2 text-emerald-950">
              <span className="mr-2 font-semibold">
                {notApplied ? "聊天提议" : "更新后"}
              </span>
              <span className="text-break whitespace-pre-wrap">
                {formatAuditValue(change.after)}
              </span>
            </div>
          )}
        </div>
      )}
      {detailAvailable && change.evidence && (
        <p className="mt-2 text-xs leading-5 text-slate-600">
          依据：{change.evidence}
        </p>
      )}
    </div>
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

function ScenarioCosts({
  scenarios,
  totalCost,
}: {
  scenarios: ScenarioCost[];
  totalCost: number;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">钱花在哪些业务动作上</h2>
        <p className="panel-subtitle">
          每一行都是一种真实模型用途；“模型请求”不等于用户消息数。
        </p>
      </div>
      {scenarios.length ? (
        <div className="divide-y divide-slate-100">
          {scenarios.map((item) => {
            const meta = scenarioMeta(item.scenario);
            const average = item.calls
              ? item.estimated_cost_yuan / item.calls
              : 0;
            const share = totalCost
              ? (item.estimated_cost_yuan / totalCost) * 100
              : 0;
            return (
              <div key={item.scenario} className="px-5 py-4 sm:px-6">
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_repeat(3,minmax(7rem,auto))] lg:items-start">
                  <div className="max-w-[65ch]">
                    <div className="font-bold text-slate-950">{meta.label}</div>
                    <p className="mt-1 text-sm leading-6 text-slate-600">
                      {meta.description}
                    </p>
                  </div>
                  <SmallDatum
                    label="模型请求"
                    value={`${formatNumber(item.calls)} 次`}
                  />
                  <SmallDatum label="合计花费" value={formatCost(item.estimated_cost_yuan)} />
                  <SmallDatum label="平均每次" value={formatCost(average)} />
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-cyan-600"
                      style={{ width: `${Math.max(2, share)}%` }}
                    />
                  </div>
                  <span className="tabular w-16 text-right text-xs font-semibold text-slate-600">
                    占 {share.toFixed(1)}%
                  </span>
                </div>
                <details className="mt-2 text-xs text-slate-600">
                  <summary className="cursor-pointer font-semibold text-cyan-800">
                    查看技术用量
                  </summary>
                  <p className="mt-1 leading-5">
                    成功 {formatNumber(item.successful_calls)} / {formatNumber(item.calls)} 次；
                    输入 {formatNumber(item.prompt_tokens)} Tokens，输出{" "}
                    {formatNumber(item.completion_tokens)} Tokens。记录代码：{item.scenario}
                  </p>
                </details>
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

function CaseCosts({ cases }: { cases: CaseCost[] }) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <h2 className="panel-title">每个用户的钱具体花在哪</h2>
        <p className="panel-subtitle">
          按花费从高到低排列；展开后可查看该服务单的业务动作明细。
        </p>
      </div>
      {cases.length ? (
        <div className="divide-y divide-slate-100">
          {cases.map((item) => {
            const topScenario = item.scenarios[0];
            return (
              <details key={item.service_case_id} className="group">
                <summary className="cursor-pointer list-none px-5 py-4 transition-colors hover:bg-cyan-50/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-600 sm:px-6 [&::-webkit-details-marker]:hidden">
                  <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_repeat(3,minmax(7rem,auto))_1.25rem] sm:items-center">
                    <div>
                      <div className="font-bold text-slate-950">
                        {item.user_name || "未命名用户"}
                      </div>
                      <div className="mt-1 text-xs text-slate-600">
                        {item.case_no}
                        {topScenario
                          ? ` · 主要花在${scenarioMeta(topScenario.scenario).label}`
                          : ""}
                      </div>
                    </div>
                    <SmallDatum label="模型请求" value={`${formatNumber(item.calls)} 次`} />
                    <SmallDatum label="合计花费" value={formatCost(item.estimated_cost_yuan)} />
                    <SmallDatum
                      label="最后发生"
                      value={formatChinaShortDateTime(item.last_call_at)}
                    />
                    <ChevronDown className="hidden h-5 w-5 text-slate-400 transition-transform duration-200 group-open:rotate-180 sm:block" />
                  </div>
                </summary>
                <div className="border-t border-slate-100 bg-slate-50/70 px-5 py-4 sm:px-6">
                  <div className="divide-y divide-slate-200 border-y border-slate-200">
                    {item.scenarios.map((scenario) => (
                      <div
                        key={scenario.scenario}
                        className="grid gap-2 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_7rem_7rem] sm:items-center"
                      >
                        <div>
                          <span className="font-semibold text-slate-900">
                            {scenarioMeta(scenario.scenario).label}
                          </span>
                          <span className="ml-2 text-xs text-slate-500">
                            {formatNumber(scenario.calls)} 次模型请求
                          </span>
                        </div>
                        <span className="tabular text-slate-700">
                          {formatNumber(scenario.total_tokens)} Tokens
                        </span>
                        <span className="tabular font-bold text-slate-950">
                          {formatCost(scenario.estimated_cost_yuan)}
                        </span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-3">
                    <Link
                      href={`/cases/${item.service_case_id}`}
                      className="admin-link text-sm"
                    >
                      查看服务单
                    </Link>
                  </div>
                </div>
              </details>
            );
          })}
        </div>
      ) : (
        <div className="px-6 py-10 text-center text-sm text-slate-600">
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
      <div className="text-sm font-semibold text-slate-600">{label}</div>
      <div className="tabular mt-1 text-xl font-black text-slate-950">{value}</div>
    </div>
  );
}

function SmallDatum({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-semibold text-slate-600">{label}</div>
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

function scenarioMeta(scenario: string) {
  if (SCENARIO_META[scenario]) return SCENARIO_META[scenario];
  if (scenario.startsWith("knowledge_evolution_")) {
    const kind = scenario.replace("knowledge_evolution_", "");
    return {
      label: kind === "case" ? "优秀案例知识进化" : "FAQ 知识进化",
      description: "从已有业务素材生成待人工审核的知识草稿。",
    };
  }
  return {
    label: "其他模型任务",
    description: `尚未归入固定业务名称的模型调用（记录代码：${scenario}）。`,
  };
}

function triggerDescription(reason?: string | null) {
  if (!reason) return "未记录触发原因";
  return reason
    .split(",")
    .map((item) => TRIGGER_LABELS[item] || item)
    .join("；");
}

function messageRoleTone(role: string) {
  if (role === "班主任") return "pill-warn";
  if (role === "助教") return "pill-ok";
  return "pill-neutral";
}

function actionLabel(action: string) {
  return {
    created: "新建",
    added: "新增",
    updated: "更新",
    recorded: "已记录",
    count_only: "已写入",
  }[action] || "已写入";
}

function droppedReason(reason?: string) {
  return {
    unknown_tool: "模型使用了不支持的上报类型",
    bad_args: "字段格式不符合结构化要求",
    checkin_date_not_in_batch: "打卡日期不在本批聊天范围内",
    followup_empty_answer: "追问回答为空",
    followup_not_in_whitelist: "不是本批允许处理的追问题目",
    checklist_empty_answer: "打卡清单回答为空",
    checklist_not_in_whitelist: "不是当前有效的打卡清单项",
    duplicate_memory_report: "同一批重复上报小结，只保留第一份",
    duplicate_case_candidate: "同一批重复上报案例候选，只保留第一份",
  }[reason || ""] || reason || "未通过确定性校验";
}

function formatAuditValue(value: string) {
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return Object.entries(parsed)
        .map(([key, item]) => {
          const label =
            {
              question: "问题",
              answer: "参考回答",
              background: "背景",
              case: "案例正文",
            }[key] || key;
          return `${label}：${String(item ?? "")}`;
        })
        .join("\n");
    }
  } catch {
    // 普通文本直接展示。
  }
  return value;
}
