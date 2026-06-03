import Link from "next/link";
import { ReactNode } from "react";
import { api } from "@/lib/api";

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

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">数据看板</h1>
        <p className="mt-1 text-sm text-zinc-500">服务概览与关键指标</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label="今日新增用户"
          value={summary.new_users_today}
          icon="➕"
          color="blue"
          href="/users?filter=new_today"
          description="今天新创建且未删除的服务单。"
        />
        <StatCard
          label="风险用户"
          value={summary.risk_users}
          icon="⚠️"
          color="red"
          href="/users?filter=risk"
          description="服务单命中过孕哺期、慢病、胸痛心悸、严重失眠、医疗承诺等风险词，需要人工关注。"
        />
        <StatCard
          label="待人工介入"
          value={summary.human_intervention_users ?? 0}
          icon="🚨"
          color="red"
          href="/users?filter=human_intervention"
          description="LLM 识别到健康红线、客诉退款、医疗边界、强续购等需要真人处理的服务单。"
        />
        <StatCard
          label="未跟进（2-7天）"
          value={summary.unfollowed_users}
          icon="⏳"
          color="yellow"
          href="/users?filter=unfollowed"
          description="服务中，且最近 2-7 天没有用户入站消息的服务单；与流失用户互斥。"
        />
        <StatCard
          label="续购跟进窗口"
          value={renewalFollowupUsers}
          icon="🛒"
          color="green"
          href="/users?filter=renewal_followup"
          description="按服务天数推算：已服务 18-28 天，适合人工做续购沟通，不代表用户已表达购买意愿。"
        />
        <StatCard
          label="明确续购意向"
          value={summary.renewal_intent_users ?? 0}
          icon="💚"
          color="green"
          href="/users?filter=renewal_intent"
          description="用户聊天中出现续费、复购、再买、继续购买等表达。"
        />
        <StatCard
          label="退款/退出意向"
          value={summary.refund_intent_users ?? 0}
          icon="↩"
          color="red"
          href="/users?filter=refund_intent"
          description="用户聊天中出现退款、退费、不继续、退出、退群等表达。"
        />
        <StatCard
          label="流失用户"
          value={summary.lost_users}
          icon="📉"
          color="orange"
          href="/users?filter=lost"
          description="服务中，且超过 7 天没有用户入站消息的服务单；不再计入未跟进。"
        />
        <StatCard
          label="服务单总数"
          value={summary.service_cases_total}
          icon="📋"
          color="blue"
          href="/users"
          description="当前未删除服务单总数。"
        />
        <StatCard
          label="服务中"
          value={summary.service_cases_in_service}
          icon="🟢"
          color="green"
          href="/users?filter=in_service"
          description="状态为服务中的服务单。"
        />
        <StatCard
          label="今日打卡"
          value={summary.checkins_today}
          icon="✅"
          color="yellow"
          href="/checkins"
          description="今天提交的打卡记录数。"
        />
        <StatCard
          label="今日用户消息"
          value={summary.messages_today}
          icon="💬"
          color="purple"
          href="/users?filter=messaged_today"
          description={
            <>
              今天收到的用户入站消息数。今日社群双向消息共{" "}
              <span className="mx-0.5 px-1.5 py-0.5 rounded-md bg-purple-100/90 text-purple-700 font-semibold border border-purple-200/60 text-[11px]">
                {summary.messages_today + (summary.messages_outbound_today ?? 0)}
              </span>{" "}
              条（其中用户发送{" "}
              <span className="mx-0.5 px-1.5 py-0.5 rounded-md bg-blue-100/90 text-blue-700 font-semibold border border-blue-200/60 text-[11px]">
                {summary.messages_today}
              </span>{" "}
              条，班主任/机器人回复{" "}
              <span className="mx-0.5 px-1.5 py-0.5 rounded-md bg-zinc-100/90 text-zinc-700 font-semibold border border-zinc-200/60 text-[11px]">
                {summary.messages_outbound_today ?? 0}
              </span>{" "}
              条）。
            </>
          }
        />
        <StatCard
          label="今日情绪预警"
          value={summary.emotional_users_today ?? 0}
          icon="🌧️"
          color="red"
          href="/users?filter=human_intervention"
          description="今天入站消息中，经语义识别出现明显负面情绪、执行挫败或心理风险线索的用户数。"
        />
        <StatCard
          label="待审核文件"
          value={summary.pending_documents}
          icon="📄"
          color="orange"
          href="/documents"
          description="每个服务单当前有效的最新文档里，状态为草稿或待审核的文件数。历史版本不计入。"
        />
        <AICostCard summary={summary.ai_cost_summary} />
      </div>

      <OutcomeSummaryPanel summary={summary.outcome_summary} />

      {/* 开发者系统健康自测试 Banner 引导 */}
      <div className="mt-8 p-6 rounded-2xl border border-indigo-100 bg-gradient-to-r from-indigo-50/80 via-white to-zinc-50/50 shadow-sm flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="flex h-2 w-2 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.5)]"></span>
            <h3 className="text-sm font-bold text-zinc-900 flex items-center gap-1.5">
              ⚡ 自动化链路自我进化与诊断中心
            </h3>
          </div>
          <p className="text-xs text-zinc-500 leading-relaxed max-w-3xl">
            系统集成了 7 项核心功能自动化链路测试（包含真实大模型多轮对话、处方 Word 编译、24小时群聊 FAQ 沉淀、服务阶段案例沉淀）。测试执行支持全自动数据库物理回滚清理，对线上生产数据零污染。
          </p>
        </div>
        <div>
          <Link
            href="/system-diagnostics"
            className="inline-flex items-center justify-center px-4.5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition active:translate-y-0.5 whitespace-nowrap"
          >
            进入诊断控制台 →
          </Link>
        </div>
      </div>
    </div>
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
  icon: string;
  color: string;
  href: string;
  description: ReactNode;
}) {
  const colorClasses: Record<string, string> = {
    blue: "bg-blue-50 border-blue-200",
    green: "bg-green-50 border-green-200",
    yellow: "bg-yellow-50 border-yellow-200",
    purple: "bg-purple-50 border-purple-200",
    orange: "bg-orange-50 border-orange-200",
    red: "bg-red-50 border-red-200",
  };

  return (
    <Link
      href={href}
      title={typeof description === "string" ? description : undefined}
      className={`block rounded-xl border p-6 transition hover:-translate-y-0.5 hover:shadow-sm ${colorClasses[color] || "bg-zinc-50 border-zinc-200"}`}
    >
      <div className="flex items-center justify-between">
        <span className="text-2xl">{icon}</span>
        <span className="text-3xl font-bold text-zinc-900">{value}</span>
      </div>
      <div className="mt-2 text-sm font-medium text-zinc-600">{label}</div>
      <p className="mt-2 text-xs leading-5 text-zinc-500">{description}</p>
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
      className="block rounded-xl border border-indigo-100 bg-gradient-to-br from-indigo-50 via-white to-sky-50 p-6 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-indigo-700">
            <span>🧠</span>
            AI 调用成本
          </div>
          <div className="mt-3 text-3xl font-bold tracking-tight text-zinc-950">
            {hasData ? `¥${cost.toFixed(2)}` : "暂无成本数据"}
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            按当前模型单价回算
          </p>
        </div>
        <span className="rounded-full border border-indigo-100 bg-white/80 px-2.5 py-1 text-xs font-semibold text-indigo-600">
          估算
        </span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-lg border border-indigo-100 bg-white/75 px-3 py-2">
          <div className="text-zinc-400">调用</div>
          <div className="mt-1 font-semibold text-zinc-700">{calls.toLocaleString("zh-CN")} 次</div>
        </div>
        <div className="rounded-lg border border-indigo-100 bg-white/75 px-3 py-2">
          <div className="text-zinc-400">Tokens</div>
          <div className="mt-1 font-semibold text-zinc-700">{tokens.toLocaleString("zh-CN")}</div>
        </div>
      </div>
      <p className="mt-3 text-xs leading-5 text-zinc-500">
        {hasData ? "点击查看用户明细。" : "产生新的 LLM 调用后会自动记录。"}
      </p>
    </Link>
  );
}
