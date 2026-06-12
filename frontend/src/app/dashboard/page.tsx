import Link from "next/link";
import type { ComponentType, ReactNode } from "react";
import { api } from "@/lib/api";
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  ClipboardList,
  Clock3,
  FileText,
  HeartHandshake,
  LifeBuoy,
  MessageCircle,
  Plus,
  RefreshCcw,
  ShoppingCart,
  ShieldAlert,
  TrendingDown,
  UserCheck,
} from "lucide-react";

type Summary = {
  service_cases_total: number;
  service_cases_in_service: number;
  checkins_today: number;
  messages_today: number;
  messages_outbound_today?: number;
  pending_documents: number;
  new_users_today: number;
  risk_users: number;
  human_intervention_users?: number;
  unfollowed_users: number;
  renewal_followup_users?: number;
  repurchase_soon_users: number;
  renewal_intent_users?: number;
  refund_intent_users?: number;
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
  needs_human_intervention: boolean;
  stage_reports: string[];
  last_inbound_at?: string | null;
};

type AICostSummary = {
  total_calls: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_yuan: number;
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
    messages_today: 0,
    messages_outbound_today: 0,
    pending_documents: 0,
    new_users_today: 0,
    risk_users: 0,
    human_intervention_users: 0,
    unfollowed_users: 0,
    renewal_followup_users: 0,
    repurchase_soon_users: 0,
    renewal_intent_users: 0,
    refund_intent_users: 0,
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
    },
  }));
  const renewalFollowupUsers = summary.renewal_followup_users ?? summary.repurchase_soon_users;
  const caseHealth = await api<CaseHealthItem[]>("/dashboard/case-health").catch(() => [] as CaseHealthItem[]);

  return (
    <div className="page-stack">
      <div>
        <h1 className="page-heading">数据看板</h1>
        <p className="page-subtitle">服务概览与关键指标</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <StatCard
          label="今日新增用户"
          value={summary.new_users_today}
          icon={Plus}
          color="blue"
          href="/users?filter=new_today"
          description="今天新创建且未删除的服务单。"
        />
        <StatCard
          label="风险用户"
          value={summary.risk_users}
          icon={AlertTriangle}
          color="red"
          href="/users?filter=risk"
          description="服务单命中过孕哺期、慢病、胸痛心悸、严重失眠、医疗承诺等风险词，需要人工关注。"
        />
        <StatCard
          label="待人工介入"
          value={summary.human_intervention_users ?? 0}
          icon={ShieldAlert}
          color="red"
          href="/users?filter=human_intervention"
          description="LLM 识别到健康红线、客诉退款、医疗边界、强续购等需要真人处理的服务单。"
        />
        <StatCard
          label="未跟进（2-7天）"
          value={summary.unfollowed_users}
          icon={Clock3}
          color="yellow"
          href="/users?filter=unfollowed"
          description="服务中，且最近 2-7 天没有用户入站消息的服务单；与流失用户互斥。"
        />
        <StatCard
          label="续购跟进窗口"
          value={renewalFollowupUsers}
          icon={ShoppingCart}
          color="green"
          href="/users?filter=renewal_followup"
          description="按服务天数推算：已服务 18-28 天，适合人工做续购沟通，不代表用户已表达购买意愿。"
        />
        <StatCard
          label="明确续购意向"
          value={summary.renewal_intent_users ?? 0}
          icon={HeartHandshake}
          color="green"
          href="/users?filter=renewal_intent"
          description="用户聊天中出现续费、复购、再买、继续购买等表达。"
        />
        <StatCard
          label="退款/退出意向"
          value={summary.refund_intent_users ?? 0}
          icon={RefreshCcw}
          color="red"
          href="/users?filter=refund_intent"
          description="用户聊天中出现退款、退费、不继续、退出、退群等表达。"
        />
        <StatCard
          label="流失用户"
          value={summary.lost_users}
          icon={TrendingDown}
          color="orange"
          href="/users?filter=lost"
          description="服务中，且超过 7 天没有用户入站消息的服务单；不再计入未跟进。"
        />
        <StatCard
          label="服务单总数"
          value={summary.service_cases_total}
          icon={ClipboardList}
          color="blue"
          href="/users"
          description="当前未删除服务单总数。"
        />
        <StatCard
          label="服务中"
          value={summary.service_cases_in_service}
          icon={UserCheck}
          color="green"
          href="/users?filter=in_service"
          description="状态为服务中的服务单。"
        />
        <StatCard
          label="今日打卡"
          value={summary.checkins_today}
          icon={CheckCircle2}
          color="yellow"
          href="/checkins"
          description="今天提交的打卡记录数。"
        />
        <StatCard
          label="今日用户消息"
          value={summary.messages_today}
          icon={MessageCircle}
          color="cyan"
          href="/users?filter=messaged_today"
          description={
            <>
              今天收到的用户入站消息数。今日社群双向消息共{" "}
              <span className="mx-0.5 rounded-md border border-cyan-200 bg-cyan-50 px-1.5 py-0.5 text-[11px] font-semibold text-cyan-800">
                {summary.messages_today + (summary.messages_outbound_today ?? 0)}
              </span>{" "}
              条（其中用户发送{" "}
              <span className="mx-0.5 rounded-md border border-sky-200 bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-800">
                {summary.messages_today}
              </span>{" "}
              条，班主任/机器人回复{" "}
              <span className="mx-0.5 rounded-md border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[11px] font-semibold text-slate-700">
                {summary.messages_outbound_today ?? 0}
              </span>{" "}
              条）。
            </>
          }
        />
        <StatCard
          label="今日情绪预警"
          value={summary.emotional_users_today ?? 0}
          icon={LifeBuoy}
          color="red"
          href="/users?filter=human_intervention"
          description="今天入站消息中，经语义识别出现明显负面情绪、执行挫败或心理风险线索的用户数。"
        />
        <StatCard
          label="待审核文件"
          value={summary.pending_documents}
          icon={FileText}
          color="orange"
          href="/documents"
          description="每个服务单当前有效的最新文档里，状态为草稿或待审核的文件数。历史版本不计入。"
        />
        <AICostCard summary={summary.ai_cost_summary} />
      </div>

      <OutcomeSummaryPanel summary={summary.outcome_summary} />
      <CaseHealthTable items={caseHealth} />
    </div>
  );
}

function CaseHealthTable({ items }: { items: CaseHealthItem[] }) {
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white shadow-sm">
      <div className="border-b border-zinc-100 px-6 py-5">
        <h2 className="text-lg font-bold text-zinc-900">在服务用户巡检</h2>
        <p className="mt-1 text-sm text-zinc-500">
          每个服务中群的服务天数、最近打卡、当前重点、风险与阶段报告进度；点击进入服务单。
        </p>
      </div>
      {items.length === 0 ? (
        <div className="p-8 text-center text-sm text-zinc-400">暂无服务中的用户。</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-zinc-100 text-left text-xs text-zinc-500">
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
      )}
    </section>
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
    <tr className="border-b border-zinc-50 hover:bg-zinc-50/60">
      <td className="px-4 py-3">
        <Link href={`/cases/${item.service_case_id}`} className="font-medium text-cyan-700 hover:underline">
          {item.user_name || "未命名"}
        </Link>
        <div className="text-xs text-zinc-400">{item.group_id}</div>
      </td>
      <td className="px-4 py-3 text-zinc-700">第 {item.service_day} 天</td>
      <td className={`px-4 py-3 ${checkinStale ? "font-medium text-rose-600" : "text-zinc-700"}`}>{checkinText}</td>
      <td className="px-4 py-3 text-zinc-600">{item.action_focus || "—"}</td>
      <td className="px-4 py-3">
        {item.risk_level === "high" ? (
          <span className="rounded bg-rose-50 px-2 py-0.5 text-xs text-rose-700">高风险</span>
        ) : item.needs_human_intervention ? (
          <span className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">待人工</span>
        ) : (
          <span className="text-xs text-zinc-400">正常</span>
        )}
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1">
          {stageOrder.map((stage) => (
            <span
              key={stage}
              title={`${stageNum[stage]}天报告`}
              className={`inline-flex h-5 min-w-5 items-center justify-center rounded px-1 text-[11px] ${
                item.stage_reports.includes(stage) ? "bg-cyan-600 text-white" : "bg-zinc-100 text-zinc-300"
              }`}
            >
              {stageNum[stage]}
            </span>
          ))}
          {item.stage_reports.includes("graduation") && (
            <span className="ml-1 rounded bg-emerald-600 px-1.5 text-[11px] text-white">毕业</span>
          )}
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
    <section className="rounded-2xl border border-zinc-200 bg-white shadow-sm">
      <div className="border-b border-zinc-100 px-6 py-5">
        <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
          <div>
            <h2 className="text-lg font-bold text-zinc-900">客户成效引擎</h2>
            <p className="mt-1 text-sm text-zinc-500">
              汇总真实执行数据、成效证据和体感反馈；样本不足时不硬下结论。
            </p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-zinc-500">
            <span className="rounded-full bg-zinc-100 px-3 py-1">有效服务单 {summary?.active_cases ?? 0}</span>
            <span className="rounded-full bg-zinc-100 px-3 py-1">7日样本 {summary?.eligible_day7_cases ?? 0}</span>
            <span className="rounded-full bg-zinc-100 px-3 py-1">28日样本 {summary?.eligible_day28_cases ?? 0}</span>
          </div>
        </div>
      </div>

      <div className="grid gap-4 p-6 lg:grid-cols-3">
        {primaryMetrics.map((metric) => (
          <OutcomeMetricCard key={metric.key} metric={metric} />
        ))}
        {!primaryMetrics.length && (
          <div className="col-span-full rounded-xl border border-dashed border-zinc-200 p-8 text-center text-sm text-zinc-500">
            暂无成效指标，等用户聊天、打卡和阶段快照沉淀后会自动显示。
          </div>
        )}
      </div>

      {!!signalMetrics.length && (
        <div className="border-t border-zinc-100 px-6 py-5">
          <h3 className="text-sm font-semibold text-zinc-900">经营信号</h3>
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
    <div className="rounded-xl border border-zinc-200 bg-zinc-50/60 p-5">
      <div className="text-sm font-medium text-zinc-600">{metric.label}</div>
      <div className="mt-3 flex items-end gap-2">
        <span className={noSample ? "text-base font-semibold text-zinc-400" : "text-3xl font-bold text-zinc-950"}>
          {formatOutcomeValue(metric)}
        </span>
        {metric.status !== "no_sample" && metric.unit && (
          <span className="pb-1 text-sm font-medium text-zinc-500">{metric.unit}</span>
        )}
      </div>
      <div className="mt-2 text-xs text-zinc-500">{formatOutcomeDenominator(metric)}</div>
      <p className="mt-3 text-xs leading-5 text-zinc-500">{metric.description}</p>
    </div>
  );
}

function OutcomeSignalRow({ metric }: { metric: OutcomeMetric }) {
  return (
    <div className="rounded-xl border border-zinc-200 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium text-zinc-700">{metric.label}</span>
        <span className="text-sm font-bold text-zinc-950">
          {formatOutcomeValue(metric)}
          {metric.status !== "no_sample" ? metric.unit : ""}
        </span>
      </div>
      <p className="mt-2 text-xs leading-5 text-zinc-500">{metric.description}</p>
    </div>
  );
}

function formatOutcomeValue(metric: OutcomeMetric) {
  if (metric.status === "no_sample" || metric.value === null || metric.value === undefined) {
    return "暂无样本";
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
  const colorClasses: Record<string, { card: string; icon: string }> = {
    blue: { card: "border-sky-200 bg-sky-50/70", icon: "bg-white text-sky-700" },
    cyan: { card: "border-cyan-200 bg-cyan-50/70", icon: "bg-white text-cyan-700" },
    green: { card: "border-emerald-200 bg-emerald-50/70", icon: "bg-white text-emerald-700" },
    yellow: { card: "border-amber-200 bg-amber-50/70", icon: "bg-white text-amber-700" },
    orange: { card: "border-orange-200 bg-orange-50/70", icon: "bg-white text-orange-700" },
    red: { card: "border-rose-200 bg-rose-50/70", icon: "bg-white text-rose-700" },
  };
  const tone = colorClasses[color] ?? { card: "border-slate-200 bg-white", icon: "bg-slate-50 text-slate-600" };

  return (
    <Link
      href={href}
      title={typeof description === "string" ? description : undefined}
      className={`block rounded-xl border p-5 shadow-sm transition-colors duration-200 hover:border-cyan-300 hover:bg-white ${tone.card}`}
    >
      <div className="flex items-center justify-between">
        <span className={`flex h-10 w-10 items-center justify-center rounded-lg shadow-sm ${tone.icon}`}>
          <Icon className="h-5 w-5" />
        </span>
        <span className="text-3xl font-bold tracking-tight text-slate-950">{value}</span>
      </div>
      <div className="mt-4 text-sm font-semibold text-slate-700">{label}</div>
      <p className="mt-2 text-xs leading-5 text-slate-500">{description}</p>
    </Link>
  );
}

function AICostCard({ summary }: { summary?: AICostSummary }) {
  const calls = summary?.total_calls ?? 0;
  const tokens = summary?.total_tokens ?? 0;
  const cost = summary?.estimated_cost_yuan ?? 0;
  const hasData = calls > 0 || tokens > 0 || cost > 0;

  return (
    <Link
      href="/dashboard/ai-costs"
      title="查看每个用户的 AI 调用成本明细"
      className="block rounded-xl border border-cyan-200 bg-cyan-50/70 p-5 shadow-sm transition-colors duration-200 hover:border-cyan-300 hover:bg-white"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-cyan-800">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-white text-cyan-700 shadow-sm">
              <Brain className="h-5 w-5" />
            </span>
            AI 调用成本
          </div>
          <div className="mt-4 text-3xl font-bold tracking-tight text-slate-950">
            {hasData ? `¥${cost.toFixed(2)}` : "暂无成本数据"}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            按当前模型单价回算
          </p>
        </div>
        <span className="rounded-full border border-cyan-200 bg-white/80 px-2.5 py-1 text-xs font-semibold text-cyan-700">
          估算
        </span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
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
