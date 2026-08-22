"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  DatabaseZap,
  GitBranch,
  LoaderCircle,
  MessageSquareReply,
  ShieldAlert,
  Wrench,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";

type RateMetric = {
  value?: number | null;
  numerator: number;
  denominator: number;
};

type Coverage = {
  retrieval_counts: Record<string, number>;
  execution_counts: Record<string, number>;
  metric_group_counts: Record<string, number>;
  retrieval_family_counts: Record<string, number>;
};

export type ReplyAgentEvaluationSummary = {
  schema_version: 1;
  run_id: string;
  run_mode: "mock" | "real";
  status: "running" | "completed" | "failed" | "interrupted";
  started_at: string;
  updated_at: string;
  finished_at?: string | null;
  git_sha: string;
  dataset_id: "reply-agent-core-v1";
  dataset_version: 1;
  model: string;
  evaluation_framework: "deepeval";
  evaluation_framework_version: string;
  counts: {
    total: number;
    completed: number;
    passed: number;
    failed: number;
    errors: number;
  };
  coverage: Coverage;
  metrics: {
    task_pass_rate: RateMetric;
    retrieval_decision_accuracy: RateMetric;
    context_recall_pass_rate: RateMetric;
    context_precision_pass_rate: RateMetric;
    faithfulness_pass_rate: RateMetric;
    tool_selection_accuracy: RateMetric;
    argument_accuracy: RateMetric;
    behavior_accuracy: RateMetric;
    answer_correctness_observed: RateMetric;
  };
  usage: {
    call_count: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    prompt_cache_hit_tokens: number;
    known_estimated_cost_yuan: number;
    estimated_cost_yuan?: number | null;
    complete: boolean;
  };
  estimated_cost_yuan?: number | null;
  error?: string | null;
};

type MetricResult = {
  key: string;
  label: string;
  group: "M0" | "MR" | "MT" | "MB";
  kind: "deterministic" | "llm";
  score?: number | null;
  threshold?: number | null;
  passed?: boolean | null;
  enforced: boolean;
  reason?: string | null;
};

type ReplyAgentEvaluationCase = {
  case_id: string;
  title: string;
  source_case_ids: string[];
  execution_kind: "rag" | "agent" | "handoff";
  tags: string[];
  status: "passed" | "failed" | "error";
  user_query: string;
  expected: {
    retrieval_decision: "retrieve" | "skip";
    response_mode: string;
    metric_groups: Array<"M0" | "MR" | "MT" | "MB">;
    retrieval_families: string[];
    required_tools: string[];
    requires_handoff: boolean;
  };
  actual: {
    retrieval_decision?: "retrieve" | "skip" | null;
    response_mode?: string | null;
    response?: string | null;
    tool_names: string[];
    handoff_triggered?: boolean | null;
  };
  metrics: MetricResult[];
  failure_reasons: Array<{
    stage: string;
    code: string;
    message: string;
    severity: "info" | "warning" | "critical";
  }>;
  usage: Record<string, unknown>;
  elapsed_ms?: number | null;
};

type ReplyAgentEvaluationRunDetail = {
  summary: ReplyAgentEvaluationSummary;
  cases: ReplyAgentEvaluationCase[];
};

export type ReplyAgentEvaluationRunList = {
  items: ReplyAgentEvaluationSummary[];
};

type FilterKey = "all" | "MR" | "MT" | "MB" | "failed";

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "all", label: "全部" },
  { key: "MR", label: "知识检索" },
  { key: "MT", label: "工具任务" },
  { key: "MB", label: "行为边界" },
  { key: "failed", label: "失败项" },
];

const FAMILY_LABELS: Record<string, string> = {
  dosage_scale: "用量刻度",
  timing_missed: "时间漏喝",
  product_pairing: "搭配切换",
  brewing_usage: "冲泡使用",
  adverse_reaction: "疑似不良反应",
  multi_turn_resolution: "多轮指代",
  mixed_report_question: "上报夹提问",
  retrieval_interference: "干扰与冲突",
};

function percent(metric: RateMetric) {
  return metric.value == null ? "—" : `${(metric.value * 100).toFixed(1)}%`;
}

function statusTone(status: ReplyAgentEvaluationSummary["status"]) {
  if (status === "completed") return "text-emerald-700 bg-emerald-50";
  if (status === "running") return "text-sky-700 bg-sky-50";
  return "text-rose-700 bg-rose-50";
}

function RouteCell({
  icon,
  value,
  label,
  tone,
}: {
  icon: ReactNode;
  value: number;
  label: string;
  tone: string;
}) {
  return (
    <div className={`min-w-0 rounded-2xl border px-4 py-3 ${tone}`}>
      <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.14em] opacity-75">
        {icon}
        {label}
      </div>
      <p className="mt-1 text-2xl font-black tabular-nums">{value}</p>
    </div>
  );
}

function MetricCard({ label, metric }: { label: string; metric: RateMetric }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3.5 py-3">
      <p className="text-xs font-semibold leading-5 text-slate-500">{label}</p>
      <div className="mt-1 flex items-end justify-between gap-3">
        <p className="text-xl font-black tabular-nums text-slate-900">{percent(metric)}</p>
        <p className="text-[11px] tabular-nums text-slate-400">
          {metric.numerator}/{metric.denominator}
        </p>
      </div>
    </div>
  );
}

function CaseStatus({ status }: { status: ReplyAgentEvaluationCase["status"] }) {
  if (status === "passed") {
    return <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />;
  }
  return status === "failed" ? (
    <XCircle className="h-4 w-4 shrink-0 text-rose-600" />
  ) : (
    <ShieldAlert className="h-4 w-4 shrink-0 text-amber-600" />
  );
}

function MetricEvidence({ metric }: { metric: MetricResult }) {
  const state = metric.passed == null ? "观察" : metric.passed ? "通过" : "失败";
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded bg-slate-200 px-1.5 py-0.5 font-mono text-[10px] font-bold text-slate-600">
            {metric.group}
          </span>
          <p className="text-xs font-bold text-slate-800">{metric.label}</p>
        </div>
        <span
          className={`text-[11px] font-bold ${
            metric.passed == null
              ? "text-slate-500"
              : metric.passed
                ? "text-emerald-700"
                : "text-rose-700"
          }`}
        >
          {state}
          {metric.score != null ? ` · ${metric.score.toFixed(3)}` : ""}
          {metric.threshold != null ? ` / 门槛 ${metric.threshold.toFixed(3)}` : ""}
        </span>
      </div>
      {metric.reason ? (
        <p className="mt-1.5 whitespace-pre-wrap text-[11px] leading-5 text-slate-500">
          {metric.reason}
        </p>
      ) : null}
    </div>
  );
}

function CaseRow({ item }: { item: ReplyAgentEvaluationCase }) {
  return (
    <details className="group border-b border-slate-100 last:border-b-0">
      <summary className="flex cursor-pointer list-none items-start gap-3 px-4 py-3.5 hover:bg-slate-50/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-500">
        <CaseStatus status={item.status} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[11px] font-bold text-slate-400">{item.case_id}</span>
            <p className="text-sm font-bold text-slate-800">{item.title}</p>
            {item.expected.metric_groups.map((group) => (
              <span
                key={group}
                className="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] font-bold text-slate-500"
              >
                {group}
              </span>
            ))}
          </div>
          <p className="mt-1 truncate text-xs text-slate-500">{item.user_query}</p>
        </div>
        <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-slate-400 transition-transform group-open:rotate-180" />
      </summary>
      <div className="grid gap-4 bg-slate-50/50 px-4 pb-5 pt-1 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
        <div className="space-y-3">
          <div className="rounded-xl border border-slate-200 bg-white p-3.5">
            <p className="text-[11px] font-bold uppercase tracking-[0.13em] text-slate-400">本题契约</p>
            <dl className="mt-2 grid grid-cols-[88px_1fr] gap-x-3 gap-y-1.5 text-xs leading-5">
              <dt className="text-slate-400">检索决策</dt>
              <dd className="font-semibold text-slate-700">{item.expected.retrieval_decision}</dd>
              <dt className="text-slate-400">回复方式</dt>
              <dd className="font-semibold text-slate-700">{item.expected.response_mode}</dd>
              <dt className="text-slate-400">必须工具</dt>
              <dd className="font-semibold text-slate-700">
                {item.expected.required_tools.join("、") || "无"}
              </dd>
              <dt className="text-slate-400">实际工具</dt>
              <dd className="font-semibold text-slate-700">
                {item.actual.tool_names.join("、") || "无"}
              </dd>
            </dl>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-3.5">
            <p className="text-[11px] font-bold uppercase tracking-[0.13em] text-slate-400">最终回复</p>
            <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-slate-700">
              {item.actual.response || (item.actual.handoff_triggered ? "已转人工，未进入常规回复 Agent。" : "无回复正文")}
            </p>
          </div>
          {item.failure_reasons.length ? (
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-3.5">
              <p className="text-[11px] font-bold uppercase tracking-[0.13em] text-rose-600">失败证据</p>
              {item.failure_reasons.map((reason) => (
                <p key={`${reason.stage}-${reason.code}`} className="mt-2 text-xs leading-5 text-rose-800">
                  {reason.message}
                </p>
              ))}
            </div>
          ) : null}
        </div>
        <div className="space-y-2">
          {item.metrics.map((metric) => (
            <MetricEvidence key={`${metric.group}-${metric.key}`} metric={metric} />
          ))}
        </div>
      </div>
    </details>
  );
}

export function ReplyAgentEvaluationsClient({
  initialRuns,
}: {
  initialRuns: ReplyAgentEvaluationSummary[];
}) {
  const [selectedRunId, setSelectedRunId] = useState(initialRuns[0]?.run_id ?? "");
  const [detail, setDetail] = useState<ReplyAgentEvaluationRunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<FilterKey>("all");

  useEffect(() => {
    if (!selectedRunId) return;
    let active = true;
    setLoading(true);
    api<ReplyAgentEvaluationRunDetail>(`/reply-agent-evaluations/${selectedRunId}`)
      .then((value) => {
        if (active) setDetail(value);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedRunId]);

  const summary = detail?.summary ?? initialRuns.find((run) => run.run_id === selectedRunId);
  const filteredCases = useMemo(() => {
    const cases = detail?.cases ?? [];
    if (filter === "all") return cases;
    if (filter === "failed") return cases.filter((item) => item.status !== "passed");
    return cases.filter((item) => item.expected.metric_groups.includes(filter));
  }, [detail, filter]);

  if (!initialRuns.length) {
    return (
      <div className="empty-state py-20">
        <Bot className="h-8 w-8 text-slate-300" />
        <p className="mt-3 text-base font-bold text-slate-800">还没有统一评测批次</p>
        <p className="mt-1 max-w-md text-xs leading-5 text-slate-500">
          运行统一回复 Agent 评测后，80 道题会在这里按检索、工具和行为边界汇总。
        </p>
      </div>
    );
  }

  if (!summary) return null;

  const retrieve = summary.coverage.retrieval_counts.retrieve ?? 0;
  const skip = summary.coverage.retrieval_counts.skip ?? 0;
  const tools = summary.coverage.metric_group_counts.MT ?? 0;
  const behavior = summary.coverage.metric_group_counts.MB ?? 0;

  return (
    <div className="space-y-4">
      <section className="panel overflow-hidden">
        <div className="border-b border-slate-200 bg-[linear-gradient(120deg,#f8fbff_0%,#ffffff_48%,#f6fbf8_100%)] px-4 py-4 sm:px-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-base font-black text-slate-900">一套题库，一次看完整回复链路</p>
                <span className={`rounded-full px-2 py-1 text-[11px] font-bold ${statusTone(summary.status)}`}>
                  {summary.run_mode === "mock" ? "Mock" : "真实"} · {summary.status}
                </span>
              </div>
              <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">
                每题先检查基础交付，再按需要追加知识检索、工具或行为边界指标；原生答案相关性只观察，不参与门禁。
              </p>
            </div>
            <label className="flex min-w-0 flex-col gap-1 text-[11px] font-bold text-slate-500">
              评测批次
              <select
                value={selectedRunId}
                onChange={(event) => setSelectedRunId(event.target.value)}
                className="min-w-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100"
              >
                {initialRuns.map((run) => (
                  <option key={run.run_id} value={run.run_id}>
                    {formatChinaShortDateTime(run.started_at)} · {run.run_mode} · {run.git_sha.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-[1.05fr_1fr_1fr_1fr_1fr]">
            <RouteCell icon={<MessageSquareReply className="h-4 w-4" />} value={summary.counts.total} label="全部题目" tone="border-slate-200 bg-white text-slate-800" />
            <RouteCell icon={<DatabaseZap className="h-4 w-4" />} value={retrieve} label="检索知识" tone="border-sky-200 bg-sky-50 text-sky-900" />
            <RouteCell icon={<GitBranch className="h-4 w-4" />} value={skip} label="跳过检索" tone="border-slate-200 bg-slate-50 text-slate-700" />
            <RouteCell icon={<Wrench className="h-4 w-4" />} value={tools} label="工具契约" tone="border-violet-200 bg-violet-50 text-violet-900" />
            <RouteCell icon={<ShieldAlert className="h-4 w-4" />} value={behavior} label="行为边界" tone="border-amber-200 bg-amber-50 text-amber-900" />
          </div>
        </div>

        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
          <MetricCard label="整题通过" metric={summary.metrics.task_pass_rate} />
          <MetricCard label="检索决策" metric={summary.metrics.retrieval_decision_accuracy} />
          <MetricCard label="召回率门禁" metric={summary.metrics.context_recall_pass_rate} />
          <MetricCard label="精确率门禁" metric={summary.metrics.context_precision_pass_rate} />
          <MetricCard label="忠实度门禁" metric={summary.metrics.faithfulness_pass_rate} />
          <MetricCard label="工具选择" metric={summary.metrics.tool_selection_accuracy} />
          <MetricCard label="工具参数" metric={summary.metrics.argument_accuracy} />
          <MetricCard label="行为边界" metric={summary.metrics.behavior_accuracy} />
        </div>

        <div className="grid gap-3 border-t border-slate-100 px-4 py-3 text-xs text-slate-500 sm:grid-cols-3">
          <div className="flex items-center gap-2">
            <CircleDollarSign className="h-4 w-4 text-slate-400" />
            费用 {summary.usage.estimated_cost_yuan == null ? `至少 ¥${summary.usage.known_estimated_cost_yuan.toFixed(4)}` : `¥${summary.usage.estimated_cost_yuan.toFixed(4)}`}
          </div>
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-slate-400" />
            {summary.usage.call_count} 次调用 · {summary.usage.total_tokens.toLocaleString()} Token
          </div>
          <div className="flex items-center gap-2 sm:justify-end">
            DeepEval {summary.evaluation_framework_version} · {summary.dataset_id}
          </div>
        </div>
      </section>

      <section className="panel overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-black text-slate-900">逐题证据</p>
            <p className="mt-0.5 text-xs text-slate-500">显示 {filteredCases.length} / {detail?.cases.length ?? 0} 题</p>
          </div>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="题目筛选">
            {FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setFilter(item.key)}
                className={`rounded-lg px-2.5 py-1.5 text-xs font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 ${
                  filter === item.key
                    ? "bg-slate-900 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin" />
            正在读取逐题证据
          </div>
        ) : (
          <div>
            {filteredCases.map((item) => (
              <CaseRow key={item.case_id} item={item} />
            ))}
          </div>
        )}
      </section>

      <section className="panel p-4">
        <p className="text-xs font-black text-slate-800">32 道检索题的八类覆盖</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(summary.coverage.retrieval_family_counts).map(([family, count]) => (
            <span key={family} className="rounded-full border border-sky-100 bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-800">
              {FAMILY_LABELS[family] ?? family} {count}
            </span>
          ))}
        </div>
      </section>
    </div>
  );
}
