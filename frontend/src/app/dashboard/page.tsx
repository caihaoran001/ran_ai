import Link from "next/link";
import type { ComponentType, ReactNode } from "react";
import { api } from "@/lib/api";
import { SystemStatusWidget } from "./system-status";
import {
  AlertTriangle,
  ArrowUpRight,
  Brain,
  CheckCircle2,
  ClipboardList,
  Clock3,
  FileText,
  HeartHandshake,
  LifeBuoy,
  LogOut,
  MessageCircle,
  Minus,
  Plus,
  RefreshCcw,
  ShoppingCart,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  UserCheck,
} from "lucide-react";

type Summary = {
  service_cases_total: number;
  service_cases_in_service: number;
  checkins_today: number;
  complete_checkins_today: number;
  messages_today: number;
  messages_outbound_today?: number;
  pending_documents: number;
  new_users_today: number;
  risk_users: number;
  plateau_users?: number;
  rebound_users?: number;
  handoff_events_today?: number;
  unfollowed_users: number;
  renewal_followup_users?: number;
  repurchase_soon_users: number;
  renewal_intent_users?: number;
  refund_intent_users?: number;
  refund_pending_users?: number;
  service_cases_member_left: number;
  lost_users: number;
  emotional_users_today?: number;
  outcome_summary?: OutcomeSummary;
  ai_cost_summary?: AICostSummary;
};

type CaseHealthItem = {
  service_case_id: number;
  case_no: string;
  user_name?: string | null;
  group_id: string;
  service_day: number;
  last_checkin_date?: string | null;
  days_since_checkin?: number | null;
  action_focus?: string | null;
  risk_level?: string | null;
  stage_reports: string[];
  last_inbound_at?: string | null;
};

type AICostSummary = {
  total_calls: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_yuan: number;
  deepseek_cost_yuan: number;
  vision_cost_yuan: number;
};

type OutcomeMetric = {
  key: string;
  label: string;
  value?: number | null;
  unit: string;
  numerator?: number | null;
  denominator?: number | null;
  description: string;
  status: string;
};

type OutcomeSummary = {
  active_cases: number;
  eligible_day7_cases: number;
  eligible_day28_cases: number;
  metrics: OutcomeMetric[];
};

export default async function DashboardPage() {
  const summary = await api<Summary>("/dashboard/summary").catch(() => ({
    service_cases_total: 0,
    service_cases_in_service: 0,
    checkins_today: 0,
    complete_checkins_today: 0,
    messages_today: 0,
    messages_outbound_today: 0,
    pending_documents: 0,
    new_users_today: 0,
    risk_users: 0,
    plateau_users: 0,
    rebound_users: 0,
    handoff_events_today: 0,
    unfollowed_users: 0,
    renewal_followup_users: 0,
    repurchase_soon_users: 0,
    renewal_intent_users: 0,
    refund_intent_users: 0,
    refund_pending_users: 0,
    service_cases_member_left: 0,
    lost_users: 0,
    emotional_users_today: 0,
    outcome_summary: {
      active_cases: 0,
      eligible_day7_cases: 0,
      eligible_day28_cases: 0,
      metrics: [],
    },
    ai_cost_summary: {
      total_calls: 0,
      total_tokens: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      estimated_cost_yuan: 0,
      deepseek_cost_yuan: 0,
      vision_cost_yuan: 0,
    },
  }));
  const renewalFollowupUsers = summary.renewal_followup_users ?? summary.repurchase_soon_users;
  const caseHealth = await api<CaseHealthItem[]>("/dashboard/case-health").catch(() => [] as CaseHealthItem[]);

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">数据看板</h1>
            <p className="page-subtitle">服务概览、风险信号与运营质量巡检。优先处理红色风险，再看今日执行和服务规模。</p>
          </div>
          <SystemStatusWidget />
        </div>
      </div>

      <section className="relative overflow-hidden rounded-2xl border border-slate-800 bg-slate-950 text-white shadow-lg">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-slate-900/70 via-slate-950 to-slate-950" />
        <div
          className="pointer-events-none absolute -right-16 -top-24 h-72 w-72 rounded-full bg-cyan-500/10 blur-[100px]"
        />
        <div className="relative grid gap-0 lg:grid-cols-[1.2fr_2fr]">
          <div className="border-b border-slate-800/80 p-5 lg:border-b-0 lg:border-r lg:p-6">
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-cyan-300">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
              今日运营指挥台
            </div>
            <div className="mt-4 flex items-baseline gap-2">
              <span className="tabular text-4xl font-black tracking-tight">{summary.service_cases_in_service}</span>
              <span className="text-base font-semibold text-slate-300">个服务中社群</span>
            </div>
            <p className="mt-3 max-w-xl text-sm leading-6 text-slate-400">
              先看转人工、退款和情绪风险，再处理待审核文件与今日触达。
            </p>
          </div>
          <div className="grid grid-cols-2 divide-x divide-y divide-slate-800/80 sm:grid-cols-4 sm:divide-y-0">
            <CommandMetric label="风险待处理" value={(summary.handoff_events_today ?? 0) + (summary.refund_intent_users ?? 0) + (summary.refund_pending_users ?? 0) + (summary.emotional_users_today ?? 0)} tone="risk" />
            <CommandMetric label="今日消息" value={summary.messages_today} tone="info" />
            <CommandMetric label="今日有打卡" value={summary.checkins_today} tone="work" />
            <CommandMetric label="待审核文件" value={summary.pending_documents} tone="review" />
          </div>
        </div>
      </section>

      <DashboardMetricSection title="风险优先处理" description="这些信号需要运营先看证据，再决定是否介入。">
        <StatCard label="转人工事件" value={summary.handoff_events_today ?? 0} icon={ShieldAlert} color="red" href="/dashboard/attention-events" description="今日触发转人工并 @班主任 的事件数；点击查看服务单、触发原话和班主任接手记录。" />
        <StatCard label="退款/退出意向" value={summary.refund_intent_users ?? 0} icon={RefreshCcw} color="red" href="/dashboard/refund-intents" description="用户聊天中出现退款、退费、不继续、退出、退群等表达；点击查看触发原话。" />
        <StatCard label="已退款待确认" value={summary.refund_pending_users ?? 0} icon={RefreshCcw} color="red" href="/dashboard/refund-pending" description="AI 识别到客户可能已完成退款、等待运营确认；确认后停止打卡/阶段报告等主动推送，群聊仍保留。" />
        <StatCard label="今日情绪预警" value={summary.emotional_users_today ?? 0} icon={LifeBuoy} color="red" href="/dashboard/emotional-alerts" description="今天入站消息中，经语义识别出现明显负面情绪、执行挫败或心理风险线索的用户数。" />
        <StatCard label="平台期" value={summary.plateau_users ?? 0} icon={Minus} color="orange" href="/users?filter=plateau" description="最后一次称重往前一周体重没动静（数据不足时看用户是否自述卡住）；点击查看名单，详情页有判定依据。" />
        <StatCard label="体重反弹" value={summary.rebound_users ?? 0} icon={TrendingUp} color="red" href="/users?filter=rebound" description="最后一次称重往前一周的体重趋势上涨超过 1 公斤，比平台期更该尽快介入。" />
      </DashboardMetricSection>

      <DashboardMetricSection title="今日运营节奏" description="用于判断当天服务触达、审核和跟进压力。">
        <StatCard label="今日用户消息" value={summary.messages_today} icon={MessageCircle} color="cyan" href="/dashboard/today-messages" description={<>今天收到的用户入站消息数。今日社群双向消息共 <span className="font-bold text-cyan-900">{summary.messages_today + (summary.messages_outbound_today ?? 0)}</span> 条。</>} />
        <StatCard label="今日有打卡" value={summary.checkins_today} icon={CheckCircle2} color="yellow" href="/dashboard/today-checkins" description={`今天有打卡记录 ${summary.checkins_today} 条，其中完整打卡 ${summary.complete_checkins_today} 条；点击按服务单查看。`} />
        <StatCard label="待审核文件" value={summary.pending_documents} icon={FileText} color="orange" href="/dashboard/pending-documents" description="当前有效最新文档中，草稿或待审核的文件数；点击查看具体服务单和文件。" />
        <StatCard label="今日新增用户" value={summary.new_users_today} icon={Plus} color="blue" href="/users?filter=new_today" description="今天提交入营问卷的服务单。群里有人说话就会自动建单（老群换人也会），但客户真正进来的凭据是交问卷，所以按问卷算。" />
      </DashboardMetricSection>

      <DashboardMetricSection title="服务规模与生命周期" description="用于安排续购、未跟进和流失处理优先级。">
        <StatCard label="服务中" value={summary.service_cases_in_service} icon={UserCheck} color="green" href="/users?filter=in_service" description="状态为服务中的服务单。" />
        <StatCard label="已退群" value={summary.service_cases_member_left} icon={LogOut} color="orange" href="/users?filter=member_left" description="客户本人已退群、系统已自动完结的服务单；误退的客户回群后会自动恢复。" />
        <StatCard label="服务单总数" value={summary.service_cases_total} icon={ClipboardList} color="blue" href="/users" description="当前未删除服务单总数：只含走过入营问卷的新群，已退款的单也计入；老群不进统计。群体画像同一口径。" />
        <StatCard label="风险用户" value={summary.risk_users} icon={AlertTriangle} color="red" href="/dashboard/risk-users" description="服务单命中过孕哺期、慢病、胸痛心悸、严重失眠、医疗承诺等风险，需要人工关注。" />
        <StatCard label="未跟进（2-7天）" value={summary.unfollowed_users} icon={Clock3} color="yellow" href="/dashboard/unfollowed" description="服务中，且用户本人已 2-7 天没有发言的服务单（班主任发言不计）；点击查看闲置天数。" />
        <StatCard label="流失用户" value={summary.lost_users} icon={TrendingDown} color="orange" href="/dashboard/lost" description="服务中，且用户本人已超过 7 天没有发言的服务单（班主任发言不计）；点击查看闲置天数。" />
        <StatCard label="续购跟进窗口" value={renewalFollowupUsers} icon={ShoppingCart} color="green" href="/dashboard/renewal-followup" description="已服务 18-28 天，适合人工做续购沟通；不代表用户已表达购买意愿。" />
        <StatCard label="明确续购意向" value={summary.renewal_intent_users ?? 0} icon={HeartHandshake} color="green" href="/dashboard/renewal-intents" description="用户聊天中出现续费、复购、再买、继续购买等表达；点击查看触发原话。" />
        <AICostCard summary={summary.ai_cost_summary} />
      </DashboardMetricSection>

      <OutcomeSummaryPanel summary={summary.outcome_summary} />
      <CaseHealthTable items={caseHealth} />
    </div>
  );
}

function CaseHealthTable({ items }: { items: CaseHealthItem[] }) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <h2 className="panel-title">在服务用户巡检</h2>
        <p className="panel-subtitle">
          每个服务中群的服务天数、最近打卡、当前重点、风险与阶段报告进度；点击进入服务单。
        </p>
      </div>
      {items.length === 0 ? (
        <div className="p-8 text-center text-sm text-slate-500">暂无服务中的用户。</div>
      ) : (
        <>
          <div className="grid gap-3 p-4 md:hidden">
            {items.map((item) => (
              <CaseHealthMobileCard key={item.service_case_id} item={item} />
            ))}
          </div>
          <div className="hidden overflow-x-auto md:block">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs text-slate-500">
                <th className="px-4 py-3 font-medium">用户 / 群</th>
                <th className="px-4 py-3 font-medium">服务天数</th>
                <th className="px-4 py-3 font-medium">最近打卡</th>
                <th className="px-4 py-3 font-medium">当前行动重点</th>
                <th className="px-4 py-3 font-medium">风险</th>
                <th className="px-4 py-3 font-medium">阶段报告</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <CaseHealthRow key={item.service_case_id} item={item} />
              ))}
            </tbody>
          </table>
          </div>
        </>
      )}
    </section>
  );
}

function CommandMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "risk" | "info" | "work" | "review";
}) {
  const toneClass: Record<typeof tone, string> = {
    risk: "text-rose-300",
    info: "text-cyan-300",
    work: "text-emerald-300",
    review: "text-amber-300",
  };
  return (
    <div className="p-4 transition-colors duration-200 hover:bg-white/[0.03] sm:p-5">
      <div className={`tabular text-3xl font-black tracking-tight ${toneClass[tone]}`}>{value}</div>
      <div className="mt-2 text-xs font-semibold text-slate-400">{label}</div>
    </div>
  );
}

function DashboardMetricSection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="panel-title">{title}</h2>
            <p className="panel-subtitle">{description}</p>
          </div>
        </div>
      </div>
      <div className="grid gap-3 p-4 sm:grid-cols-2 sm:p-5 xl:grid-cols-4">
        {children}
      </div>
    </section>
  );
}

function CaseHealthMobileCard({ item }: { item: CaseHealthItem }) {
  const checkinText =
    item.last_checkin_date == null
      ? "未打卡"
      : item.days_since_checkin === 0
      ? "今天"
      : `${item.days_since_checkin} 天前`;
  const checkinStale = item.last_checkin_date == null || (item.days_since_checkin ?? 0) >= 2;

  return (
    <Link
      href={`/cases/${item.service_case_id}`}
      prefetch={false}
      className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition-colors hover:border-cyan-300 hover:bg-cyan-50/30"
    >
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-break font-semibold text-slate-950">{item.user_name || "未命名"}</div>
          <div className="text-break mt-1 text-xs text-slate-500">{item.group_id}</div>
        </div>
        <span className="status-pill border-emerald-200 bg-emerald-50 text-emerald-700">
          {item.service_day < 1 ? "未开始" : `第 ${item.service_day} 天`}
        </span>
      </div>
      <div className="mt-3 grid gap-2 text-sm">
        <div className={checkinStale ? "font-semibold text-rose-600" : "text-slate-700"}>最近打卡：{checkinText}</div>
        <div className="text-break text-slate-700">行动重点：{item.action_focus || "暂无"}</div>
        <div className="flex flex-wrap items-center gap-2">
          {item.risk_level === "high" ? (
            <span className="status-pill border-rose-200 bg-rose-50 text-rose-700">高风险</span>
          ) : (
            <span className="status-pill border-slate-200 bg-slate-50 text-slate-600">正常</span>
          )}
          <span className="text-xs text-slate-500">阶段报告 {item.stage_reports.length} 项</span>
        </div>
      </div>
    </Link>
  );
}

function CaseHealthRow({ item }: { item: CaseHealthItem }) {
  const checkinText =
    item.last_checkin_date == null
      ? "未打卡"
      : item.days_since_checkin === 0
      ? "今天"
      : `${item.days_since_checkin} 天前`;
  const checkinStale = item.last_checkin_date == null || (item.days_since_checkin ?? 0) >= 2;
  const stageOrder = ["day3", "day7", "day14", "day21", "day28"];
  const stageNum: Record<string, string> = { day3: "3", day7: "7", day14: "14", day21: "21", day28: "28" };

  return (
    <tr className="border-b border-slate-50 hover:bg-slate-50/60">
      <td className="px-4 py-3">
        <Link
          href={`/cases/${item.service_case_id}`}
          prefetch={false}
          className="font-medium text-cyan-700 hover:underline"
        >
          {item.user_name || "未命名"}
        </Link>
        <div className="text-xs text-slate-400">{item.group_id}</div>
      </td>
      <td className="px-4 py-3 text-slate-700">{item.service_day < 1 ? "未开始" : `第 ${item.service_day} 天`}</td>
      <td className={`px-4 py-3 ${checkinStale ? "font-medium text-rose-600" : "text-slate-700"}`}>{checkinText}</td>
      <td className="px-4 py-3 text-slate-600">{item.action_focus || "—"}</td>
      <td className="px-4 py-3">
        {item.risk_level === "high" ? (
          <span className="rounded bg-rose-50 px-2 py-0.5 text-xs text-rose-700">高风险</span>
        ) : (
          <span className="text-xs text-slate-400">正常</span>
        )}
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1">
          {stageOrder.map((stage) => (
            <span
              key={stage}
              title={`${stageNum[stage]}天报告`}
              className={
                item.stage_reports.includes(stage)
                  ? "inline-flex h-5 min-w-5 items-center justify-center rounded bg-cyan-600 px-1 text-[11px] text-white"
                  : "inline-flex h-5 min-w-5 items-center justify-center rounded bg-slate-100 px-1 text-[11px] text-slate-500"
              }
            >
              {stageNum[stage]}
            </span>
          ))}
        </div>
      </td>
    </tr>
  );
}

function OutcomeSummaryPanel({ summary }: { summary?: OutcomeSummary }) {
  const metrics = summary?.metrics ?? [];
  const primaryKeys = new Set([
    "profile_completion_rate",
    "core_document_generation_rate",
    "day7_snapshot_rate",
    "day28_checkin_completion_rate",
    "result_signal_rate",
    "body_feedback_rate",
  ]);
  const primaryMetrics = metrics.filter((metric) => primaryKeys.has(metric.key));
  const signalMetrics = metrics.filter((metric) => !primaryKeys.has(metric.key));

  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
          <div>
            <h2 className="panel-title">客户成效引擎</h2>
            <p className="panel-subtitle">
              汇总真实执行数据、成效证据和体感反馈；样本不足时不硬下结论。
            </p>
          </div>
          <div className="flex flex-col items-start gap-2 md:items-end">
            <Link
              href="/dashboard/outcome-trends"
              prefetch={false}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-cyan-200 bg-cyan-50 px-3 text-sm font-semibold text-cyan-800 transition-colors hover:border-cyan-300 hover:bg-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-600 focus-visible:ring-offset-2"
            >
              查看月度趋势 <ArrowUpRight className="h-4 w-4" />
            </Link>
            <div className="flex flex-wrap gap-2 text-xs text-slate-500">
              <span className="rounded-full bg-slate-100 px-3 py-1">有效服务单 {summary?.active_cases ?? 0}</span>
              <span className="rounded-full bg-slate-100 px-3 py-1">7日样本 {summary?.eligible_day7_cases ?? 0}</span>
              <span className="rounded-full bg-slate-100 px-3 py-1">28日样本 {summary?.eligible_day28_cases ?? 0}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-4 p-4 sm:p-5 lg:grid-cols-3">
        {primaryMetrics.map((metric) => (
          <OutcomeMetricCard key={metric.key} metric={metric} />
        ))}
        {!primaryMetrics.length && (
          <div className="col-span-full rounded-xl border border-dashed border-slate-200 p-8 text-center text-sm text-slate-500">
            暂无成效指标，等用户聊天、打卡和阶段快照沉淀后会自动显示。
          </div>
        )}
      </div>

      {!!signalMetrics.length && (
        <div className="border-t border-slate-100 px-4 py-5 sm:px-5">
          <h3 className="text-sm font-semibold text-slate-900">经营信号</h3>
          <div className="mt-3 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {signalMetrics.map((metric) => (
              <OutcomeSignalRow key={metric.key} metric={metric} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function OutcomeMetricCard({ metric }: { metric: OutcomeMetric }) {
  const noSample = metric.status === "no_sample";
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-5">
      <div className="text-sm font-medium text-slate-600">{metric.label}</div>
      <div className="mt-3 flex items-end gap-2">
        <span className={noSample ? "text-base font-semibold text-slate-400" : "text-3xl font-bold text-slate-950"}>
          {formatOutcomeValue(metric)}
        </span>
        {metric.status !== "no_sample" && metric.unit && (
          <span className="pb-1 text-sm font-medium text-slate-500">{metric.unit}</span>
        )}
      </div>
      <div className="mt-2 text-xs text-slate-500">{formatOutcomeDenominator(metric)}</div>
      <p className="mt-3 text-xs leading-5 text-slate-500">{metric.description}</p>
    </div>
  );
}

// 体重/腰围两个指标点进去能看分窗口、分人群和样本口径。
const OUTCOME_METRIC_LINKS: Record<string, string> = {
  day28_weight_change_median: "/dashboard/body-change",
  day28_waist_change_median: "/dashboard/body-change",
};

function OutcomeSignalRow({ metric }: { metric: OutcomeMetric }) {
  const href = OUTCOME_METRIC_LINKS[metric.key];
  const body = (
    <>
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium text-slate-700">
          {metric.label}
          {href && <ArrowUpRight className="ml-1 inline h-3.5 w-3.5 text-cyan-600" />}
        </span>
        <span className="text-sm font-bold text-slate-950">
          {formatOutcomeValue(metric)}
          {metric.status !== "no_sample" ? metric.unit : ""}
        </span>
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-500">{metric.description}</p>
    </>
  );
  if (href) {
    return (
      <Link
        href={href}
        prefetch={false}
        className="block rounded-xl border border-slate-200 px-4 py-3 transition hover:border-cyan-300 hover:bg-cyan-50/40"
      >
        {body}
      </Link>
    );
  }
  return <div className="rounded-xl border border-slate-200 px-4 py-3">{body}</div>;
}

function formatOutcomeValue(metric: OutcomeMetric) {
  if (metric.status === "no_sample" || metric.value === null || metric.value === undefined) {
    return "暂无样本";
  }
  // 体征变化是有方向的：负数代表瘦了/小了，原来只显示裸数字会被读反
  // （线上那个「9.1cm」其实是变大方向，却被当成缩减 9 厘米）。
  if (metric.unit === "kg" || metric.unit === "cm") {
    if (metric.value === 0) return "持平";
    const magnitude = Math.abs(metric.value).toFixed(1).replace(/\.0$/, "");
    return `${metric.value < 0 ? "减少" : "增加"} ${magnitude}`;
  }
  if (Number.isInteger(metric.value)) {
    return String(metric.value);
  }
  return metric.value.toFixed(1).replace(/\.0$/, "");
}

function formatOutcomeDenominator(metric: OutcomeMetric) {
  if (metric.denominator === null || metric.denominator === undefined) {
    return "来自阶段快照聚合";
  }
  if (metric.numerator === null || metric.numerator === undefined) {
    return `样本 ${metric.denominator}`;
  }
  return `样本 ${metric.numerator ?? 0}/${metric.denominator}`;
}

function StatCard({
  label,
  value,
  icon,
  color,
  href,
  description,
}: {
  label: string;
  value: number | string;
  icon: ComponentType<{ className?: string }>;
  color: string;
  href: string;
  description: ReactNode;
}) {
  const Icon = icon;
  const colorClasses: Record<string, { card: string; icon: string; value: string }> = {
    blue: { card: "", icon: "bg-sky-50 text-sky-700 ring-sky-100", value: "text-slate-900" },
    cyan: { card: "", icon: "bg-cyan-50 text-cyan-700 ring-cyan-100", value: "text-slate-900" },
    green: { card: "", icon: "bg-emerald-50 text-emerald-700 ring-emerald-100", value: "text-slate-900" },
    yellow: { card: "", icon: "bg-amber-50 text-amber-700 ring-amber-100", value: "text-slate-900" },
    orange: { card: "", icon: "bg-orange-50 text-orange-700 ring-orange-100", value: "text-slate-900" },
    red: { card: "!border-rose-200/80 bg-gradient-to-b from-rose-50/50 to-white", icon: "bg-rose-100 text-rose-700 ring-rose-200/70", value: "text-rose-700" },
  };
  const tone = colorClasses[color] ?? { card: "", icon: "bg-slate-100 text-slate-700 ring-slate-200", value: "text-slate-900" };

  return (
    <Link
      href={href}
      title={typeof description === "string" ? description : undefined}
      className={`metric-card group flex flex-col !p-4 ${tone.card}`}
    >
      <div className="flex items-center justify-between gap-3">
        <span className={`flex h-10 w-10 items-center justify-center rounded-xl ring-1 ${tone.icon}`}>
          <Icon className="h-5 w-5" />
        </span>
        <span className={`tabular text-[1.75rem] font-extrabold leading-none tracking-tight ${tone.value}`}>{value}</span>
      </div>
      <div className="mt-4 flex items-center gap-1.5 text-sm font-bold text-slate-800">
        {label}
        <ArrowUpRight className="h-3.5 w-3.5 text-slate-300 transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-cyan-500" />
      </div>
      <p className="mt-1.5 text-xs leading-5 text-slate-500">{description}</p>
    </Link>
  );
}

function AICostCard({ summary }: { summary?: AICostSummary }) {
  const calls = summary?.total_calls ?? 0;
  const tokens = summary?.total_tokens ?? 0;
  const cost = summary?.estimated_cost_yuan ?? 0;
  const deepseekCost = summary?.deepseek_cost_yuan ?? 0;
  const visionCost = summary?.vision_cost_yuan ?? 0;
  const hasData = calls > 0 || tokens > 0 || cost > 0;

  return (
    <Link
      href="/agent-operations?tab=costs"
      title="查看 AI Agent 运行与成本明细"
      className="block rounded-xl border border-cyan-200 bg-cyan-50/70 p-4 shadow-sm transition-colors duration-200 hover:border-cyan-300 hover:bg-white sm:p-5"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-cyan-800">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-white text-cyan-700 shadow-sm">
              <Brain className="h-5 w-5" />
            </span>
            AI 调用成本
          </div>
          <div className="mt-4 text-2xl font-bold tracking-tight text-slate-950 sm:text-3xl">
            {hasData ? `¥${cost.toFixed(2)}` : "暂无成本数据"}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            DeepSeek + 识图模型调用合计
          </p>
        </div>
        <span className="rounded-full border border-cyan-200 bg-white/80 px-2.5 py-1 text-xs font-semibold text-cyan-700">
          估算
        </span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-lg border border-cyan-100 bg-white/80 px-3 py-2">
          <div className="text-slate-400">DeepSeek</div>
          <div className="mt-1 font-semibold text-slate-700">¥{deepseekCost.toFixed(2)}</div>
        </div>
        <div className="rounded-lg border border-cyan-100 bg-white/80 px-3 py-2">
          <div className="text-slate-400">识图</div>
          <div className="mt-1 font-semibold text-slate-700">¥{visionCost.toFixed(2)}</div>
        </div>
        <div className="rounded-lg border border-cyan-100 bg-white/80 px-3 py-2">
          <div className="text-slate-400">调用</div>
          <div className="mt-1 font-semibold text-slate-700">{calls.toLocaleString("zh-CN")} 次</div>
        </div>
        <div className="rounded-lg border border-cyan-100 bg-white/80 px-3 py-2">
          <div className="text-slate-400">Tokens</div>
          <div className="mt-1 font-semibold text-slate-700">{tokens.toLocaleString("zh-CN")}</div>
        </div>
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-500">
        {hasData ? "点击查看用户明细。" : "产生新的 LLM 调用后会自动记录。"}
      </p>
    </Link>
  );
}
