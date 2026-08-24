"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  DatabaseZap,
  GitBranch,
  LoaderCircle,
  MessageSquareReply,
  Scale,
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

type AverageMetric = {
  value?: number | null;
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
  score_source?: "fresh_run" | "saved_output_reanalysis";
  source_run_id?: string | null;
  ruler_policy?: {
    retrieval_reference_policy_version: string;
    rewrite_harm_policy_version: string;
    rewrite_recall_drop_threshold: number;
    task_completion_policy_version: string;
    task_completion_threshold: number;
    task_completion_calibration_id: string;
  } | null;
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
    rewrite_harm_rate: RateMetric;
    context_recall_pass_rate: RateMetric;
    context_precision_pass_rate: RateMetric;
    faithfulness_pass_rate: RateMetric;
    tool_selection_accuracy: RateMetric;
    argument_accuracy: RateMetric;
    behavior_accuracy: RateMetric;
    handoff_accuracy: RateMetric;
    answer_correctness_observed: RateMetric;
    answer_relevancy_average: AverageMetric;
    judge_task_completion_observed: RateMetric;
    judge_groundedness_observed: RateMetric;
    judge_clarification_observed: RateMetric;
    judge_expression_observed: RateMetric;
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
    knowledge_query?: string | null;
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
  evidence?: {
    rag?: {
      actual?: {
        raw_query_hits?: Array<{ title: string; content: string }>;
        rewritten_query_hits?: Array<{ title: string; content: string }>;
      };
      scores?: {
        raw_context_recall?: number | null;
        context_recall?: number | null;
      };
    };
  };
  usage: {
    tracking_available?: boolean;
    total?: {
      call_count: number;
      total_tokens: number;
      known_estimated_cost_yuan: number;
      estimated_cost_yuan?: number | null;
      complete: boolean;
    };
  };
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

function AverageCard({ label, metric }: { label: string; metric: AverageMetric }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 px-3.5 py-3">
      <p className="text-xs font-semibold leading-5 text-slate-500">{label}</p>
      <div className="mt-1 flex items-end justify-between gap-3">
        <p className="text-xl font-black tabular-nums text-slate-900">{percent({ ...metric, numerator: 0 })}</p>
        <p className="text-[11px] tabular-nums text-slate-400">{metric.denominator} 题</p>
      </div>
    </div>
  );
}

function ComparisonRow({
  label,
  current,
  baseline,
  lowerIsBetter = false,
}: {
  label: string;
  current: RateMetric;
  baseline: RateMetric;
  lowerIsBetter?: boolean;
}) {
  const currentValue = current.value;
  const baselineValue = baseline.value;
  const delta =
    currentValue == null || baselineValue == null
      ? null
      : (currentValue - baselineValue) * 100;
  const improved =
    delta == null ? null : lowerIsBetter ? delta <= 0 : delta >= 0;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_52px_18px_52px_64px] items-center gap-2 border-t border-slate-100 py-2.5 text-xs first:border-t-0">
      <span className="truncate font-semibold text-slate-700">{label}</span>
      <span className="text-right font-mono text-slate-400">{percent(baseline)}</span>
      <ArrowRight className="h-3.5 w-3.5 text-slate-300" aria-hidden="true" />
      <span className="text-right font-mono font-bold text-slate-900">{percent(current)}</span>
      <span
        className={`text-right font-mono text-[11px] font-bold ${
          improved == null
            ? "text-slate-400"
            : improved
              ? "text-emerald-700"
              : "text-rose-700"
        }`}
      >
        {delta == null ? "—" : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}pt`}
      </span>
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
  const thresholdLabel =
    metric.threshold == null
      ? ""
      : metric.key === "rewrite_harm"
        ? ` / 门槛 ≤ ${metric.threshold.toFixed(3)}`
        : ` / 门槛 ${metric.threshold.toFixed(3)}`;
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
          {thresholdLabel}
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
  const ragEvidence = item.evidence?.rag;
  const rawHits = ragEvidence?.actual?.raw_query_hits ?? [];
  const rewrittenHits = ragEvidence?.actual?.rewritten_query_hits ?? [];
  const rawRecall = ragEvidence?.scores?.raw_context_recall;
  const rewrittenRecall = ragEvidence?.scores?.context_recall;
  const caseUsage = item.usage.total;

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
          {caseUsage ? (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-[11px] text-slate-500">
              <span className="font-bold text-slate-700">本题 API 消耗</span>
              <span>{caseUsage.call_count} 次调用</span>
              <span>{caseUsage.total_tokens.toLocaleString()} Token</span>
              <span>
                {caseUsage.estimated_cost_yuan == null ? "至少 " : ""}¥
                {caseUsage.known_estimated_cost_yuan.toFixed(4)}
              </span>
              <span>{caseUsage.complete ? "记账完整" : "存在未知用量"}</span>
            </div>
          ) : null}
          {item.expected.retrieval_decision === "retrieve" ? (
            <div className="rounded-xl border border-sky-100 bg-sky-50/60 p-3.5">
              <p className="text-[11px] font-bold uppercase tracking-[0.13em] text-sky-700">改写与召回对照</p>
              <p className="mt-2 text-xs leading-5 text-slate-700">
                <span className="font-bold text-slate-500">实际检索词：</span>
                {item.actual.knowledge_query || "未产生检索词"}
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {[
                  { label: "原问题召回", score: rawRecall, hits: rawHits },
                  { label: "改写后召回", score: rewrittenRecall, hits: rewrittenHits },
                ].map((side) => (
                  <div key={side.label} className="rounded-lg border border-sky-100 bg-white px-3 py-2.5">
                    <div className="flex items-center justify-between gap-2 text-[11px]">
                      <span className="font-bold text-slate-700">{side.label}</span>
                      <span className="font-mono text-slate-500">
                        {side.score == null ? "无评分" : side.score.toFixed(3)} · {side.hits.length} 条
                      </span>
                    </div>
                    <div className="mt-1.5 space-y-1.5">
                      {side.hits.length ? (
                        side.hits.slice(0, 3).map((hit, index) => (
                          <div key={`${hit.title}-${index}`} className="border-t border-slate-100 pt-1.5 first:border-0 first:pt-0">
                            <p className="truncate text-[11px] font-semibold text-slate-600">{hit.title}</p>
                            <p className="mt-0.5 line-clamp-2 text-[11px] leading-5 text-slate-400">{hit.content}</p>
                          </div>
                        ))
                      ) : (
                        <p className="text-[11px] leading-5 text-slate-400">没有召回内容</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
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
  const baseline = useMemo(() => {
    if (
      !summary ||
      summary.run_mode !== "real" ||
      summary.status !== "completed" ||
      summary.counts.total !== 80 ||
      summary.score_source === "saved_output_reanalysis"
    ) {
      return null;
    }
    const currentStartedAt = new Date(summary.started_at).getTime();
    return (
      initialRuns.find(
        (run) =>
          run.run_id !== summary.run_id &&
          run.run_mode === "real" &&
          run.status === "completed" &&
          run.counts.total === 80 &&
          run.score_source !== "saved_output_reanalysis" &&
          new Date(run.started_at).getTime() < currentStartedAt,
      ) ?? null
    );
  }, [initialRuns, summary]);
  const baselineReplay = useMemo(() => {
    if (!baseline) return null;
    return (
      initialRuns.find(
        (run) =>
          run.status === "completed" &&
          run.score_source === "saved_output_reanalysis" &&
          run.source_run_id === baseline.run_id,
      ) ?? null
    );
  }, [baseline, initialRuns]);

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
                <span className="rounded-full border border-slate-200 bg-white px-2 py-1 text-[11px] font-bold text-slate-500">
                  {summary.score_source === "saved_output_reanalysis" ? "存量回复重分析" : "全链路新运行"}
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

        {summary.error ? (
          <div className="border-b border-rose-200 bg-rose-50 px-4 py-3 text-xs leading-5 text-rose-800 sm:px-5">
            本批次未完整结束：{summary.error}
          </div>
        ) : null}

        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <MetricCard label="整题通过" metric={summary.metrics.task_pass_rate} />
          <MetricCard label="任务完成度门禁" metric={summary.metrics.judge_task_completion_observed} />
          <MetricCard label="检索决策" metric={summary.metrics.retrieval_decision_accuracy} />
          <MetricCard label="改写损伤（越低越好）" metric={summary.metrics.rewrite_harm_rate} />
          <MetricCard label="召回率门禁" metric={summary.metrics.context_recall_pass_rate} />
          <MetricCard label="精确率门禁" metric={summary.metrics.context_precision_pass_rate} />
          <MetricCard label="忠实度门禁" metric={summary.metrics.faithfulness_pass_rate} />
          <MetricCard label="工具选择" metric={summary.metrics.tool_selection_accuracy} />
          <MetricCard label="工具参数" metric={summary.metrics.argument_accuracy} />
          <MetricCard label="行为边界" metric={summary.metrics.behavior_accuracy} />
          <MetricCard label="高风险转人工" metric={summary.metrics.handoff_accuracy} />
        </div>

        <div className="border-t border-slate-100 bg-slate-50/40 px-4 py-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-xs font-black text-slate-700">观察指标</p>
            <p className="text-[11px] text-slate-400">展示问题趋势，不影响本批整题通过</p>
          </div>
          <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            <MetricCard label="回答正确完整" metric={summary.metrics.answer_correctness_observed} />
            <AverageCard label="答案相关平均分" metric={summary.metrics.answer_relevancy_average} />
            <MetricCard label="Agent 证据忠实" metric={summary.metrics.judge_groundedness_observed} />
            <MetricCard label="Agent 澄清合理" metric={summary.metrics.judge_clarification_observed} />
            <MetricCard label="Agent 表达质量" metric={summary.metrics.judge_expression_observed} />
          </div>
        </div>

        {summary.ruler_policy ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-slate-100 bg-white px-4 py-3 text-[11px] text-slate-500">
            <span className="flex items-center gap-1.5 font-black text-slate-700">
              <Scale className="h-4 w-4 text-sky-700" />
              本批尺子
            </span>
            <span>召回事实：{summary.ruler_policy.retrieval_reference_policy_version}</span>
            <span>改写损伤：{summary.ruler_policy.rewrite_harm_policy_version}</span>
            <span>
              任务完成：{summary.ruler_policy.task_completion_policy_version} ≥{" "}
              {(summary.ruler_policy.task_completion_threshold * 100).toFixed(0)}%
            </span>
            <span>校准集：{summary.ruler_policy.task_completion_calibration_id}</span>
          </div>
        ) : null}

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


      {baseline ? (
        <section className="panel overflow-hidden" aria-label="新旧完整基线对比">
          <div className="border-b border-slate-200 bg-slate-950 px-4 py-3 text-white sm:px-5">
            <div className="flex flex-wrap items-end justify-between gap-2">
              <div>
                <p className="text-sm font-black">新旧完整基线对比</p>
                <p className="mt-0.5 text-[11px] text-slate-300">
                  同一套 80 题；左侧 {baseline.git_sha.slice(0, 8)}，右侧 {summary.git_sha.slice(0, 8)}
                  {baselineReplay ? "；任务完成与改写损伤按新尺重算旧回复" : ""}
                </p>
              </div>
              <p className="font-mono text-[11px] text-slate-300">
                通过 {baseline.counts.passed}/80 → {summary.counts.passed}/80 · 费用 ¥
                {baseline.usage.known_estimated_cost_yuan.toFixed(4)} → ¥
                {summary.usage.known_estimated_cost_yuan.toFixed(4)}
              </p>
            </div>
          </div>
          <div className="grid gap-x-8 px-4 py-2 sm:grid-cols-2 sm:px-5">
            <div>
              <ComparisonRow label="整题通过" baseline={baseline.metrics.task_pass_rate} current={summary.metrics.task_pass_rate} />
              <ComparisonRow label="任务完成度" baseline={(baselineReplay ?? baseline).metrics.judge_task_completion_observed} current={summary.metrics.judge_task_completion_observed} />
              <ComparisonRow label="改写损伤" baseline={(baselineReplay ?? baseline).metrics.rewrite_harm_rate} current={summary.metrics.rewrite_harm_rate} lowerIsBetter />
              <ComparisonRow label="上下文召回" baseline={baseline.metrics.context_recall_pass_rate} current={summary.metrics.context_recall_pass_rate} />
            </div>
            <div>
              <ComparisonRow label="事实忠实" baseline={baseline.metrics.faithfulness_pass_rate} current={summary.metrics.faithfulness_pass_rate} />
              <ComparisonRow label="工具选择" baseline={baseline.metrics.tool_selection_accuracy} current={summary.metrics.tool_selection_accuracy} />
              <ComparisonRow label="工具参数" baseline={baseline.metrics.argument_accuracy} current={summary.metrics.argument_accuracy} />
              <ComparisonRow label="行为边界" baseline={baseline.metrics.behavior_accuracy} current={summary.metrics.behavior_accuracy} />
            </div>
          </div>
        </section>
      ) : null}

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
