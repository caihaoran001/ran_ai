"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertOctagon,
  ArrowDown,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Clock3,
  Database,
  FileSearch,
  Gauge,
  LoaderCircle,
  MessageSquareText,
  SearchCheck,
  ShieldAlert,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";

type MetricValue = {
  value?: number | null;
  numerator?: number | null;
  denominator?: number | null;
};

type RagMetricPolicy = {
  status: "enforced" | "calibration_failed" | "not_calibrated" | "mock" | "legacy";
  enforced: boolean;
  calibration_run_id?: string | null;
  threshold?: number | null;
  reason: string;
};

export type RagEvaluationSummary = {
  schema_version: 1;
  run_id: string;
  run_mode: "mock" | "real" | "unknown";
  status: "queued" | "running" | "completed" | "failed" | "interrupted";
  started_at: string;
  updated_at: string;
  finished_at?: string | null;
  git_sha: string;
  dataset_id: string;
  dataset_version: number;
  model: string;
  embedding_model: string;
  ragas_version: string;
  counts: {
    total: number;
    completed: number;
    passed: number;
    failed: number;
    skipped: number;
    errors: number;
  };
  metrics: {
    retrieval_decision_error_rate: MetricValue;
    rewrite_harm_rate: MetricValue;
    context_recall: MetricValue;
    context_precision: MetricValue;
    faithfulness: MetricValue;
    answer_relevancy: MetricValue;
  };
  answer_relevancy_policy: RagMetricPolicy;
  faithfulness_policy: RagMetricPolicy;
  critical_fact_errors: number;
  estimated_cost_yuan?: number | null;
  error?: string | null;
};

type KnowledgeHit = {
  rank: number;
  chunk_uid: string;
  title: string;
  source_type: string;
  content: string;
};

type BinaryJudgement = {
  statement: string;
  reason: string;
  verdict: number;
};

type ContextPrecisionJudgement = {
  rank: number;
  reason: string;
  verdict: number;
};

type AnswerRelevancyJudgement = {
  generated_question: string;
  noncommittal: number;
  similarity: number;
};

type MetricEvidence = {
  context_recall: BinaryJudgement[];
  raw_context_recall: BinaryJudgement[];
  context_precision: ContextPrecisionJudgement[];
  faithfulness: BinaryJudgement[];
  answer_relevancy: AnswerRelevancyJudgement[];
  answer_relevancy_all_noncommittal?: boolean | null;
};

type FailureReason = {
  stage: "decision" | "rewrite" | "retrieval" | "answer" | "ragas" | "runtime";
  code: string;
  message: string;
  severity: "info" | "warning" | "critical";
};

type EvaluationCase = {
  case_id: string;
  title: string;
  origin: {
    kind: "faq_rewrite" | "real_chat" | "unknown";
    reference_id: string;
  };
  tags: string[];
  status: "passed" | "failed" | "skipped" | "error";
  history: Array<{ role: "user" | "staff" | "assistant"; content: string }>;
  user_query: string;
  expected: {
    retrieval_decision: "retrieve" | "skip";
    response_mode?: "answer" | "acknowledge" | "clarify" | "use_runtime_data" | "support" | null;
    rewrite: {
      must_include: string[];
      must_preserve_meanings: string[];
    };
    required_facts: string[];
  };
  actual: {
    retrieval_decision?: "retrieve" | "skip" | null;
    decision_source: string;
    knowledge_query?: string | null;
    raw_query_hits: KnowledgeHit[];
    rewritten_query_hits: KnowledgeHit[];
    response?: string | null;
  };
  scores: {
    context_recall?: number | null;
    context_precision?: number | null;
    faithfulness?: number | null;
    answer_relevancy?: number | null;
  };
  metric_evidence?: MetricEvidence;
  failure_reasons: FailureReason[];
  elapsed_ms?: number | null;
};

type RagEvaluationRunDetail = {
  summary: RagEvaluationSummary;
  cases: EvaluationCase[];
};

export type RagEvaluationRunList = { items: RagEvaluationSummary[] };

type CalibrationMetrics = {
  positive_mean: number;
  negative_mean: number;
  mean_gap: number;
  auc: number;
  paired_accuracy: number;
  positive_pass_rate: number;
  negative_rejection_rate: number;
};

type MetricCalibrationReport = {
  schema_version: 1 | 2 | 3;
  calibration_id: string;
  dataset_version: number;
  run_id: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  updated_at: string;
  finished_at?: string | null;
  git_sha: string;
  model: string;
  ragas_version: string;
  threshold?: number | null;
  base_pair_count: number;
  completed_pair_count: number;
  passed?: boolean | null;
  metrics?: CalibrationMetrics | null;
  failed_criteria: string[];
};

type AnswerRelevancyCalibrationItem = {
  case_id: string;
  source_case_id?: string | null;
  user_input: string;
  positive_response: string;
  negative_response: string;
  positive_score: number;
  negative_score: number;
  positive_details: AnswerRelevancyJudgement[];
  negative_details: AnswerRelevancyJudgement[];
  positive_all_noncommittal?: boolean | null;
  negative_all_noncommittal?: boolean | null;
  phase?: "selection" | "validation" | "legacy";
  repeat_index?: number;
};

type FaithfulnessCalibrationItem = {
  case_id: string;
  user_input: string;
  positive_response: string;
  negative_response: string;
  positive_score: number;
  negative_score: number;
  positive_details: BinaryJudgement[];
  negative_details: BinaryJudgement[];
  phase?: "selection" | "validation" | "legacy";
  error_type?: string;
  repeat_index?: number;
};

type FaithfulnessStageRun = {
  repeat_index: number;
  passed: boolean;
  metrics: CalibrationMetrics;
};

type ThresholdSelection = {
  rule: "perfect_gap_midpoint_v1" | "class_rate_interval_midpoint_v1";
  minimum_classification_rate: number;
  lower_exclusive: number;
  upper_inclusive: number;
  selected_threshold: number;
  safety_margin: number;
};

export type AnswerRelevancyCalibrationReport = MetricCalibrationReport & {
  embedding_model: string;
  items: AnswerRelevancyCalibrationItem[];
  repeat_count?: number;
  selection_case_count?: number;
  validation_case_count?: number;
  selected_threshold?: number | null;
  threshold_selection?: ThresholdSelection | null;
  selection_results?: FaithfulnessStageRun[];
  validation_results?: FaithfulnessStageRun[];
};

export type FaithfulnessCalibrationReport = MetricCalibrationReport & {
  items: FaithfulnessCalibrationItem[];
  repeat_count?: number;
  selection_case_count?: number;
  validation_case_count?: number;
  selected_threshold?: number | null;
  threshold_selection?: ThresholdSelection | null;
  selection_results?: FaithfulnessStageRun[];
  validation_results?: FaithfulnessStageRun[];
};

const STATUS_LABELS: Record<RagEvaluationSummary["status"], string> = {
  queued: "等待运行",
  running: "正在评测",
  completed: "评测完成",
  failed: "运行失败",
  interrupted: "运行中断",
};

const STAGE_LABELS: Record<FailureReason["stage"], string> = {
  decision: "是否检索",
  rewrite: "问题改写",
  retrieval: "知识召回",
  answer: "最终回复",
  ragas: "RAGAS",
  runtime: "运行环境",
};

const ROLE_LABELS = { user: "用户", staff: "工作人员", assistant: "AI助教" };
const DECISION_LABELS = { retrieve: "需要检索", skip: "跳过检索" } as const;
const RUN_MODE_LABELS: Record<RagEvaluationSummary["run_mode"], string> = {
  mock: "模拟评测",
  real: "真实评测",
  unknown: "旧报告未标记",
};
const ORIGIN_LABELS: Record<EvaluationCase["origin"]["kind"], string> = {
  faq_rewrite: "FAQ 改写",
  real_chat: "真实聊天",
  unknown: "旧用例",
};
const RESPONSE_MODE_LABELS: Record<NonNullable<EvaluationCase["expected"]["response_mode"]>, string> = {
  answer: "回答问题",
  acknowledge: "确认收到",
  clarify: "追问澄清",
  use_runtime_data: "使用实时能力",
  support: "接住情绪",
};

type CaseFilter =
  | "all"
  | "passed"
  | "failed"
  | "critical"
  | "faq_rewrite"
  | "real_chat"
  | FailureReason["stage"];

function percent(value?: number | null) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function MetricLine({
  label,
  hint,
  metric,
  lowerIsBetter = false,
  threshold,
  observational = false,
}: {
  label: string;
  hint: string;
  metric: MetricValue;
  lowerIsBetter?: boolean;
  threshold?: number;
  observational?: boolean;
}) {
  const value = metric.value ?? null;
  const healthy =
    value == null || observational
      ? null
      : lowerIsBetter
        ? value === 0
        : threshold == null || value >= threshold;
  const width = value == null ? 0 : Math.max(2, Math.min(100, value * 100));
  return (
    <div className="border-b border-slate-100 py-4 last:border-b-0">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-bold text-slate-900">{label}</span>
            {observational && (
              <span className="status-pill pill-neutral">仅观察</span>
            )}
            {healthy === true && (
              <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-label="达标" />
            )}
            {healthy === false && (
              <TriangleAlert className="h-4 w-4 text-rose-600" aria-label="未达标" />
            )}
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-500">{hint}</p>
        </div>
        <div className="shrink-0 text-right">
          <div className={`text-xl font-black tabular-nums ${healthy === false ? "text-rose-700" : "text-slate-900"}`}>
            {percent(value)}
          </div>
          {metric.denominator != null && (
            <div className="mt-0.5 text-[11px] tabular-nums text-slate-400">
              {metric.numerator != null ? `${metric.numerator} / ` : ""}
              {metric.denominator} 条
            </div>
          )}
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${observational ? "bg-slate-400" : healthy === false ? "bg-rose-500" : lowerIsBetter ? "bg-cyan-500" : "bg-emerald-500"}`}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

function CaseStatus({ status }: { status: EvaluationCase["status"] }) {
  const styles = {
    passed: "pill-ok",
    failed: "pill-risk",
    skipped: "pill-neutral",
    error: "pill-warn",
  } as const;
  const labels = { passed: "通过", failed: "未通过", skipped: "跳过", error: "运行错误" };
  return <span className={`status-pill ${styles[status]}`}>{labels[status]}</span>;
}

function FactList({
  title,
  items,
}: {
  title: string;
  items: string[];
}) {
  if (!items.length) return null;
  return (
    <div>
      <p className="text-xs font-bold text-slate-600">{title}</p>
      <ul className="mt-1.5 space-y-1.5">
        {items.map((fact, index) => (
          <li key={`${fact}-${index}`} className="flex gap-2 text-sm leading-6 text-slate-700">
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan-600" />
            <span>{fact}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function VerdictBadge({ verdict }: { verdict: number }) {
  return (
    <span className={`status-pill ${verdict === 1 ? "pill-ok" : "pill-risk"}`}>
      {verdict === 1 ? "支持" : "不支持"}
    </span>
  );
}

function BinaryJudgementList({
  items,
  empty,
}: {
  items: BinaryJudgement[];
  empty: string;
}) {
  if (!items.length) {
    return <p className="text-xs leading-5 text-slate-400">{empty}</p>;
  }
  return (
    <div className="space-y-2">
      {items.map((judgement, index) => (
        <div key={`${judgement.statement}-${index}`} className="rounded-lg border border-slate-200 bg-white px-3 py-2.5">
          <div className="flex items-start justify-between gap-3">
            <p className="text-xs font-bold leading-5 text-slate-800">{judgement.statement}</p>
            <VerdictBadge verdict={judgement.verdict} />
          </div>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">
            {judgement.reason || "判官未返回文字理由。"}
          </p>
        </div>
      ))}
    </div>
  );
}

function MetricEvidencePanel({
  evidence,
  hits,
  retrievalDecision,
  hasMetricScore,
}: {
  evidence?: MetricEvidence;
  hits: KnowledgeHit[];
  retrievalDecision: EvaluationCase["actual"]["retrieval_decision"];
  hasMetricScore: boolean;
}) {
  const hasEvidence = Boolean(
    evidence && (
      evidence.context_recall.length ||
      evidence.raw_context_recall.length ||
      evidence.context_precision.length ||
      evidence.faithfulness.length ||
      evidence.answer_relevancy.length ||
      evidence.answer_relevancy_all_noncommittal != null
    ),
  );
  if (!hasEvidence || !evidence) {
    const emptyMessage = retrievalDecision === "skip"
      ? "本条按预期跳过了知识检索，因此不运行 RAGAS 四项知识问答指标。"
      : hasMetricScore
        ? "这份历史报告只保存了最终分数；新批次会显示每条事实、召回内容和回复陈述的判断依据。"
        : "本条没有完成 RAGAS 评分，因此没有可展示的判官证据。";
    return (
      <section className="mt-4 rounded-xl border border-dashed border-slate-300 bg-white/60 px-4 py-3" aria-label="RAGAS 判断明细">
        <p className="text-xs font-bold text-slate-600">RAGAS 判断明细</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">{emptyMessage}</p>
      </section>
    );
  }

  return (
    <section className="mt-4 border-t border-slate-200 pt-4" aria-label="RAGAS 判断明细">
      <div className="mb-3">
        <p className="text-xs font-extrabold text-slate-700">RAGAS 怎么得出这些分数</p>
        <p className="mt-1 text-[11px] leading-5 text-slate-500">直接展示本轮判官的拆分结果和理由，不再只给一个最终数字。</p>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <article className="rounded-xl border border-slate-200 bg-slate-100/60 p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h4 className="text-xs font-extrabold text-slate-800">事实召回率</h4>
            <span className="text-[11px] font-semibold text-slate-500">权威事实是否找全</span>
          </div>
          <BinaryJudgementList items={evidence.context_recall} empty="没有可展示的事实覆盖判断。" />
          {evidence.raw_context_recall.length > 0 && (
            <details className="mt-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
              <summary className="cursor-pointer text-[11px] font-bold text-slate-600">查看原问题召回的对照判断</summary>
              <div className="mt-2">
                <BinaryJudgementList items={evidence.raw_context_recall} empty="没有原问题召回对照。" />
              </div>
            </details>
          )}
        </article>

        <article className="rounded-xl border border-slate-200 bg-slate-100/60 p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h4 className="text-xs font-extrabold text-slate-800">召回精确率</h4>
            <span className="text-[11px] font-semibold text-slate-500">逐条判断并考虑排名</span>
          </div>
          {evidence.context_precision.length ? (
            <div className="space-y-2">
              {evidence.context_precision.map((judgement) => {
                const hit = hits.find((item) => item.rank === judgement.rank);
                return (
                  <div key={judgement.rank} className="rounded-lg border border-slate-200 bg-white px-3 py-2.5">
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-xs font-bold leading-5 text-slate-800">#{judgement.rank} {hit?.title || "召回内容"}</p>
                      <VerdictBadge verdict={judgement.verdict} />
                    </div>
                    <p className="mt-1 text-[11px] leading-5 text-slate-500">
                      {judgement.reason || "判官未返回文字理由。"}
                    </p>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs leading-5 text-slate-400">没有可展示的逐条召回判断。</p>
          )}
        </article>

        <article className="rounded-xl border border-slate-200 bg-slate-100/60 p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h4 className="text-xs font-extrabold text-slate-800">回复忠实度</h4>
            <span className="text-[11px] font-semibold text-slate-500">回复陈述是否有召回依据</span>
          </div>
          <BinaryJudgementList items={evidence.faithfulness} empty="没有可展示的回复陈述判断。" />
        </article>

        <article className="rounded-xl border border-slate-200 bg-slate-100/60 p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h4 className="text-xs font-extrabold text-slate-800">回复相关性</h4>
            {evidence.answer_relevancy_all_noncommittal != null && (
              <span className={`status-pill ${evidence.answer_relevancy_all_noncommittal ? "pill-risk" : "pill-ok"}`}>
                {evidence.answer_relevancy_all_noncommittal ? "三次均判回避" : "未被整体判回避"}
              </span>
            )}
          </div>
          {evidence.answer_relevancy.length ? (
            <div className="space-y-2">
              {evidence.answer_relevancy.map((judgement, index) => (
                <div key={`${judgement.generated_question}-${index}`} className="rounded-lg border border-slate-200 bg-white px-3 py-2.5">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-xs font-bold leading-5 text-slate-800">{judgement.generated_question}</p>
                    <span className="text-xs font-black tabular-nums text-slate-700">{percent(judgement.similarity)}</span>
                  </div>
                  <p className={`mt-1 text-[11px] font-semibold ${judgement.noncommittal ? "text-rose-600" : "text-emerald-700"}`}>
                    {judgement.noncommittal ? "本次被判为回避/含糊" : "本次被判为有明确内容"}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs leading-5 text-slate-400">没有成功生成反向问题。</p>
          )}
        </article>
      </div>
    </section>
  );
}

function EvidenceDetail({
  item,
  runMode,
  answerRelevancyEnforced,
  faithfulnessEnforced,
}: {
  item: EvaluationCase;
  runMode: RagEvaluationSummary["run_mode"];
  answerRelevancyEnforced: boolean;
  faithfulnessEnforced: boolean;
}) {
  const hits = item.actual.rewritten_query_hits;
  const actualDecision = item.actual.retrieval_decision;
  const decisionMatched = actualDecision === item.expected.retrieval_decision;
  const resultLabel = runMode === "mock" ? "模拟结果" : "实际结果";
  const scoreItems = [
    ["事实召回", item.scores.context_recall],
    ["召回精确", item.scores.context_precision],
    [faithfulnessEnforced ? "回复忠实" : "回复忠实（仅观察）", item.scores.faithfulness],
    [answerRelevancyEnforced ? "回复相关" : "回复相关（仅观察）", item.scores.answer_relevancy],
  ] as const;

  return (
    <div className="min-w-0 border-t border-slate-200 bg-slate-50/70 px-4 py-5 sm:px-6">
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white md:grid md:grid-cols-2 md:divide-x md:divide-slate-200">
        <section className="min-w-0 border-b border-slate-200 p-4 sm:p-5 md:border-b-0" aria-label="预期结果">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-extrabold text-slate-950">预期结果</h3>
              <p className="mt-1 text-[11px] font-semibold text-slate-400">
                {ORIGIN_LABELS[item.origin.kind]} · {item.origin.reference_id}
              </p>
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <span className={`status-pill ${item.origin.kind === "real_chat" ? "pill-neutral" : "pill-info"}`}>
                {ORIGIN_LABELS[item.origin.kind]}
              </span>
              <span className="status-pill pill-info">
                {DECISION_LABELS[item.expected.retrieval_decision]}
              </span>
            </div>
          </div>
          <div className="space-y-4">
            <div className="border-b border-slate-100 pb-4">
              <p className="mb-3 text-xs font-extrabold text-slate-800">问题处理预期</p>
              <div className="space-y-3">
                <FactList title="检索问题应包含" items={item.expected.rewrite.must_include} />
                <FactList title="需要保持的原意" items={item.expected.rewrite.must_preserve_meanings} />
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-extrabold text-slate-800">最终回复预期</p>
              {item.expected.response_mode && (
                <span className="status-pill pill-neutral">
                  {RESPONSE_MODE_LABELS[item.expected.response_mode]}
                </span>
              )}
            </div>
            <FactList title="回答依据的权威事实" items={item.expected.required_facts} />
            {!item.expected.rewrite.must_include.length &&
              !item.expected.rewrite.must_preserve_meanings.length &&
              !item.expected.required_facts.length && (
              <p className="text-sm leading-6 text-slate-500">
                本条只验证系统是否正确跳过知识检索，并按预期方式接话。
              </p>
            )}
          </div>
        </section>

        <section className="min-w-0 p-4 sm:p-5" aria-label={resultLabel}>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-extrabold text-slate-950">{resultLabel}</h3>
            <span className={`status-pill ${decisionMatched ? "pill-ok" : "pill-risk"}`}>
              {actualDecision ? DECISION_LABELS[actualDecision] : "未产出检索决策"}
            </span>
          </div>
          <dl className="space-y-4">
            <div>
              <dt className="text-xs font-bold text-slate-500">{runMode === "mock" ? "模拟改写问题" : "实际改写问题"}</dt>
              <dd className="mt-1 break-words text-sm font-semibold leading-6 text-slate-800">
                {item.actual.knowledge_query || "未生成知识查询"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-bold text-slate-500">{runMode === "mock" ? "模拟回复" : "实际回复"}</dt>
              <dd className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-800">
                {item.actual.response || "本条未生成回复"}
              </dd>
            </div>
          </dl>
        </section>
      </div>

      <div className="mt-4 grid min-w-0 gap-4 lg:grid-cols-2">
        <section className="min-w-0 border-t border-slate-200 pt-4">
          <div className="mb-3 flex items-center gap-2 text-xs font-bold text-slate-600">
            <MessageSquareText className="h-4 w-4 text-slate-500" /> 测试输入与最近对话
          </div>
          {item.history.length > 0 && (
            <div className="mb-3 space-y-2">
              {item.history.map((message, index) => (
                <p key={`${message.role}-${index}`} className="text-xs leading-5 text-slate-500">
                  <span className="font-bold text-slate-700">{ROLE_LABELS[message.role]}：</span>
                  {message.content}
                </p>
              ))}
            </div>
          )}
          <p className="rounded-lg bg-slate-200/60 px-3 py-2.5 text-sm font-bold leading-6 text-slate-900">
            {item.user_query}
          </p>
        </section>

        <section className="min-w-0 border-t border-slate-200 pt-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs font-bold text-slate-600">
            <span className="flex items-center gap-2">
              <Database className="h-4 w-4 text-slate-500" />
              {runMode === "mock" ? "模拟召回内容" : "实际召回内容"}
            </span>
            <span className="font-semibold text-slate-500">
              原问题 {item.actual.raw_query_hits.length} 条 · 改写后 {hits.length} 条
            </span>
          </div>
          <div className="space-y-2">
            {hits.length ? hits.slice(0, 3).map((hit) => (
              <details key={hit.chunk_uid} className="group rounded-lg border border-slate-200 bg-white px-3 py-2">
                <summary className="cursor-pointer list-none text-xs font-semibold text-slate-700">
                  #{hit.rank} {hit.title}
                </summary>
                <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-500">{hit.content}</p>
              </details>
            )) : (
              <p className="rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs text-slate-500">
                {actualDecision === "skip" ? "本条跳过知识检索" : "没有召回内容"}
              </p>
            )}
          </div>
        </section>
      </div>

      <MetricEvidencePanel
        evidence={item.metric_evidence}
        hits={hits}
        retrievalDecision={actualDecision}
        hasMetricScore={scoreItems.some(([, value]) => value != null)}
      />

      <section className="mt-4 border-t border-slate-200 pt-4" aria-label="评测判定">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-600">
              {item.status === "passed" ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              ) : (
                <TriangleAlert className="h-4 w-4 text-rose-600" />
              )}
              评测判定
            </div>
            {item.failure_reasons.length ? (
              <div className="mt-2 space-y-2">
                {item.failure_reasons.map((reason, index) => (
                  <div
                    key={`${reason.code}-${index}`}
                    className={`rounded-lg border px-3 py-2 text-xs leading-5 ${reason.severity === "critical" ? "border-rose-200 bg-rose-50 text-rose-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}
                  >
                    <span className="font-bold">{STAGE_LABELS[reason.stage]}：</span>
                    {reason.message}
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm font-semibold text-emerald-700">
                {resultLabel}符合本条预期，未发现失败原因。
              </p>
            )}
          </div>
          <div className="grid shrink-0 grid-cols-2 gap-x-5 gap-y-2 sm:grid-cols-4">
            {scoreItems.map(([label, value]) => (
              <div key={label} className="text-left sm:text-right">
                <div className="text-[11px] font-bold text-slate-500">{label}</div>
                <div className="mt-0.5 text-sm font-black tabular-nums text-slate-800">{percent(value)}</div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

type CalibrationItem = AnswerRelevancyCalibrationItem | FaithfulnessCalibrationItem;

function CalibrationAnswer({
  item,
  kind,
  side,
}: {
  item: CalibrationItem;
  kind: "relevancy" | "faithfulness";
  side: "positive" | "negative";
}) {
  const response = side === "positive" ? item.positive_response : item.negative_response;
  const score = side === "positive" ? item.positive_score : item.negative_score;
  const label = side === "positive" ? "正确回答" : kind === "relevancy" ? "错配回答" : "冲突回答";

  if (kind === "relevancy") {
    const relevancyItem = item as AnswerRelevancyCalibrationItem;
    const details = side === "positive" ? relevancyItem.positive_details : relevancyItem.negative_details;
    const allNoncommittal = side === "positive"
      ? relevancyItem.positive_all_noncommittal
      : relevancyItem.negative_all_noncommittal;
    return (
      <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-extrabold text-slate-500">{label}</p>
          <span className="text-sm font-black tabular-nums text-slate-900">{percent(score)}</span>
        </div>
        <p className="mt-2 text-xs leading-5 text-slate-700">{response}</p>
        {allNoncommittal === true && (
          <p className="mt-2 text-[11px] font-bold text-rose-700">三次均被判为回避，最终分数归零</p>
        )}
        {details.length ? (
          <div className="mt-2 space-y-1.5 border-t border-slate-100 pt-2">
            {details.map((detail, index) => (
              <div key={`${detail.generated_question}-${index}`} className="flex items-start justify-between gap-3 text-[11px] leading-5">
                <span className="text-slate-600">{detail.generated_question}</span>
                <span className={`shrink-0 font-bold tabular-nums ${detail.noncommittal ? "text-rose-600" : "text-slate-500"}`}>
                  {percent(detail.similarity)}{detail.noncommittal ? " · 回避" : ""}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-[11px] text-slate-400">历史校准未保存反向问题明细。</p>
        )}
      </div>
    );
  }

  const faithfulnessItem = item as FaithfulnessCalibrationItem;
  const details = side === "positive" ? faithfulnessItem.positive_details : faithfulnessItem.negative_details;
  return (
    <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] font-extrabold text-slate-500">{label}</p>
        <span className="text-sm font-black tabular-nums text-slate-900">{percent(score)}</span>
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-700">{response}</p>
      {details.length ? (
        <div className="mt-2 border-t border-slate-100 pt-2">
          <BinaryJudgementList items={details} empty="没有陈述判断。" />
        </div>
      ) : (
        <p className="mt-2 text-[11px] text-slate-400">历史校准未保存陈述判断明细。</p>
      )}
    </div>
  );
}

function MetricCalibrationCard({
  title,
  negativeLabel,
  calibration,
  kind,
}: {
  title: string;
  negativeLabel: string;
  calibration: AnswerRelevancyCalibrationReport | FaithfulnessCalibrationReport | null;
  kind: "relevancy" | "faithfulness";
}) {
  const completed = calibration?.status === "completed";
  const passed = completed && calibration?.passed === true;
  const metrics = calibration?.metrics ?? null;
  const twoStageCalibration = calibration as (
    | AnswerRelevancyCalibrationReport
    | FaithfulnessCalibrationReport
    | null
  );
  const hasTwoStageResult = Boolean(twoStageCalibration?.threshold_selection);
  const selectedThreshold = twoStageCalibration?.selected_threshold ?? calibration?.threshold;
  const statusLabel = passed
    ? "校准通过"
    : calibration?.status === "completed"
      ? "校准未通过"
      : calibration?.status === "running"
        ? "校准进行中"
        : calibration
          ? "校准运行失败"
          : "尚未校准";
  const tone = passed
    ? "border-emerald-200 bg-emerald-50/70"
    : "border-amber-200 bg-amber-50/75";

  return (
    <article className={`overflow-hidden rounded-xl border ${tone}`} aria-label={`${title}指标校准`}>
      <div className="px-5 py-5">
        <div className="flex min-w-0 items-start gap-3">
          <span className={`mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${passed ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
            <Gauge className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-extrabold text-slate-950">{title}</h3>
              <span className={`status-pill ${passed ? "pill-ok" : "pill-warn"}`}>
                {statusLabel}
              </span>
              {!passed && <span className="status-pill pill-neutral">新批次仅观察</span>}
            </div>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-600">
              {calibration?.status === "running"
                ? "正在对 30 题分别完成两轮判分；跑完后才会选择和验证门槛。"
                : hasTwoStageResult && passed
                ? `先用 ${twoStageCalibration?.selection_case_count} 题选择门槛，再用未参与选择的 ${twoStageCalibration?.validation_case_count} 题复核；两轮都通过后才用于正式判定。`
                : passed
                ? "固定中文正负样本已证明当前门槛可用，新批次会把该指标纳入通过判定。"
                : calibration
                  ? hasTwoStageResult
                    ? "锁定验证没有通过；新批次仍记录分数，但不会因这一项单独判失败。"
                    : "这份旧报告使用的选线方式已作废；分数仍可查看，但不会参与新批次判定。"
                  : "尚未找到中文校准报告。新批次会保留分数，但不把该指标作为硬门槛。"}
            </p>
            {calibration && (
              <p className="mt-1 text-[11px] font-semibold text-slate-400">
                {hasTwoStageResult
                  ? `${calibration.items.length}/${calibration.base_pair_count * (twoStageCalibration?.repeat_count ?? 1)} 次题目轮次`
                  : `${calibration.completed_pair_count}/${calibration.base_pair_count} 组正负对照`} · {calibration.model} · {formatChinaShortDateTime(calibration.updated_at)}
              </p>
            )}
          </div>
        </div>
        {metrics && (
          <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-slate-900/5 pt-4">
            {[
              ["正确回答均值", metrics.positive_mean],
              [negativeLabel, metrics.negative_mean],
              ["区分度 AUC", metrics.auc],
              [`正确回答过 ${percent(selectedThreshold)}`, metrics.positive_pass_rate],
            ].map(([label, value]) => (
              <div key={String(label)}>
                <dt className="text-[11px] font-bold text-slate-500">{label}</dt>
                <dd className="mt-0.5 text-lg font-black tabular-nums text-slate-900">
                  {percent(value as number)}
                </dd>
              </div>
            ))}
          </dl>
        )}
        {hasTwoStageResult && (
          <div className="mt-4 space-y-3 border-t border-slate-900/5 pt-4">
            <div>
              <p className="text-[11px] font-extrabold text-slate-500">第一步 · 20 题选门槛</p>
              <div className="mt-2 rounded-lg border border-cyan-200 bg-cyan-50/60 px-3 py-2.5">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <span className="text-sm font-black tabular-nums text-slate-900">已选 {percent(twoStageCalibration?.threshold_selection?.selected_threshold)}</span>
                  <span className="text-[10px] font-extrabold text-cyan-800">
                    {twoStageCalibration?.threshold_selection?.rule === "perfect_gap_midpoint_v1" ? "取完全分开区间中点" : "取 90% 可用范围中点"}
                  </span>
                </div>
                <p className="mt-1 text-[10px] leading-4 text-slate-600">
                  两轮共同可用范围：({percent(twoStageCalibration?.threshold_selection?.lower_exclusive)}, {percent(twoStageCalibration?.threshold_selection?.upper_inclusive)}]；中点距离两侧边界各 {percent(twoStageCalibration?.threshold_selection?.safety_margin)}。
                </p>
              </div>
              <p className="mt-1.5 text-[10px] text-slate-400">
                {twoStageCalibration?.selection_results?.map((run) => `第${run.repeat_index}轮 ${percent(run.metrics.positive_pass_rate)}/${percent(run.metrics.negative_rejection_rate)}`).join(" · ")}；每轮依次为正确回答识别率 / 错误回答拒绝率，均至少 90%。
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white/70 px-3 py-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[11px] font-extrabold text-slate-500">第二步 · 10 题锁定验证</p>
                <span className={`text-[10px] font-extrabold ${twoStageCalibration?.validation_results?.every((run) => run.passed) ? "text-emerald-700" : "text-amber-700"}`}>
                  {twoStageCalibration?.validation_results?.length
                    ? twoStageCalibration.validation_results.every((run) => run.passed) ? "两轮通过" : "存在未通过轮次"
                    : "等待选择门槛"}
                </span>
              </div>
              <p className="mt-1 text-[10px] leading-4 text-slate-500">
                {twoStageCalibration?.validation_results?.length
                  ? twoStageCalibration.validation_results.map((run) => `第${run.repeat_index}轮 ${percent(run.metrics.positive_pass_rate)}/${percent(run.metrics.negative_rejection_rate)}`).join(" · ")
                  : "第一步没有选出稳定门槛，因此不使用验证题反向调参。"}
              </p>
            </div>
          </div>
        )}
      </div>
      {calibration?.failed_criteria.length ? (
        <div className="border-t border-amber-200/70 px-5 py-3 text-xs leading-5 text-amber-900">
          未通过原因：{calibration.failed_criteria.join("；")}
        </div>
      ) : null}
      {calibration?.items.length ? (
        <details className="border-t border-slate-200/80 bg-white/60">
          <summary className="cursor-pointer px-5 py-3 text-xs font-extrabold text-slate-700">
            {hasTwoStageResult
              ? `查看 ${calibration.base_pair_count} 题 × ${twoStageCalibration?.repeat_count} 轮的 ${calibration.items.length} 次实际判分`
              : `查看 ${calibration.items.length} 组校准题和实际判分`}
          </summary>
          <div className="max-h-[36rem] space-y-3 overflow-y-auto border-t border-slate-100 px-4 py-4">
            {calibration.items.map((item, index) => (
              <article key={`${item.case_id}-${"repeat_index" in item ? item.repeat_index ?? 1 : 1}`} className="rounded-xl border border-slate-200 bg-slate-100/70 p-3">
                <div className="flex items-start gap-2">
                  <span className="flex h-5 min-w-5 items-center justify-center rounded-md bg-slate-800 px-1 text-[10px] font-black tabular-nums text-white">{index + 1}</span>
                  <div className="min-w-0">
                    <p className="text-xs font-extrabold leading-5 text-slate-900">{item.user_input}</p>
                    {"phase" in item && item.phase !== "legacy" && (
                      <p className="mt-0.5 text-[10px] font-semibold text-slate-400">
                        {item.phase === "selection" ? "选门槛题" : "锁定验证题"} · 第 {item.repeat_index ?? 1} 轮
                      </p>
                    )}
                  </div>
                </div>
                <div className="mt-3 grid gap-2 xl:grid-cols-2">
                  <CalibrationAnswer item={item} kind={kind} side="positive" />
                  <CalibrationAnswer item={item} kind={kind} side="negative" />
                </div>
              </article>
            ))}
          </div>
        </details>
      ) : null}
    </article>
  );
}

function MetricCalibrationPanel({
  answerRelevancy,
  faithfulness,
}: {
  answerRelevancy: AnswerRelevancyCalibrationReport | null;
  faithfulness: FaithfulnessCalibrationReport | null;
}) {
  return (
    <section className="panel overflow-hidden" aria-label="RAGAS 指标校准">
      <div className="panel-header-clean">
        <div className="flex items-center gap-2">
          <Gauge className="h-5 w-5 text-cyan-700" />
          <h2 className="panel-title">评测尺子校准</h2>
        </div>
        <p className="panel-subtitle">
          用同一批中文正确回答和确定性错误回答，先验证指标能否区分正误，再决定是否参与判定。
        </p>
      </div>
      <div className="grid gap-4 px-5 pb-5 sm:px-6 sm:pb-6 lg:grid-cols-2">
        <MetricCalibrationCard
          title="回复相关性"
          negativeLabel="错配回答均值"
          calibration={answerRelevancy}
          kind="relevancy"
        />
        <MetricCalibrationCard
          title="回复忠实度"
          negativeLabel="冲突回答均值"
          calibration={faithfulness}
          kind="faithfulness"
        />
      </div>
    </section>
  );
}

export function RagEvaluationsClient({
  initialRuns,
  initialCalibration,
  initialFaithfulnessCalibration,
}: {
  initialRuns: RagEvaluationSummary[];
  initialCalibration: AnswerRelevancyCalibrationReport | null;
  initialFaithfulnessCalibration: FaithfulnessCalibrationReport | null;
}) {
  const [runs, setRuns] = useState(initialRuns);
  const [selectedRunId, setSelectedRunId] = useState(initialRuns[0]?.run_id ?? "");
  const [detail, setDetail] = useState<RagEvaluationRunDetail | null>(null);
  const [loading, setLoading] = useState(Boolean(initialRuns.length));
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<CaseFilter>("all");
  const [expandedCase, setExpandedCase] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    api<RagEvaluationRunDetail>(`/rag-evaluations/${selectedRunId}`)
      .then((payload) => {
        if (!cancelled) {
          setDetail(payload);
          setRuns((current) =>
            current.map((item) =>
              item.run_id === payload.summary.run_id ? payload.summary : item,
            ),
          );
          setExpandedCase((current) =>
            current ?? payload.cases.find((item) => item.status !== "passed")?.case_id ?? payload.cases[0]?.case_id ?? null,
          );
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : "报告读取失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  const summary = detail?.summary ?? runs.find((run) => run.run_id === selectedRunId) ?? null;
  const visibleCases = useMemo(() => {
    const cases = detail?.cases ?? [];
    if (filter === "all") return cases;
    if (filter === "passed") return cases.filter((item) => item.status === "passed");
    if (filter === "failed") return cases.filter((item) => item.status !== "passed");
    if (filter === "critical") {
      return cases.filter((item) =>
        item.failure_reasons.some((reason) => reason.severity === "critical"),
      );
    }
    if (filter === "faq_rewrite" || filter === "real_chat") {
      return cases.filter((item) => item.origin.kind === filter);
    }
    return cases.filter((item) =>
      item.failure_reasons.some((reason) => reason.stage === filter),
    );
  }, [detail, filter]);
  const originCounts = useMemo(() => {
    const cases = detail?.cases ?? [];
    return {
      faq: cases.filter((item) => item.origin.kind === "faq_rewrite").length,
      chat: cases.filter((item) => item.origin.kind === "real_chat").length,
      retrieve: cases.filter((item) => item.expected.retrieval_decision === "retrieve").length,
      skip: cases.filter((item) => item.expected.retrieval_decision === "skip").length,
    };
  }, [detail]);
  const isMockRun = summary?.run_mode === "mock";
  const isLegacyRun = summary?.run_mode === "unknown";
  const resultLabel = isMockRun ? "模拟结果" : "实际结果";

  if (!initialRuns.length) {
    return (
      <div className="page-stack">
        <header className="page-header">
          <div className="page-header-row">
            <div>
              <p className="section-label">固定题库验收</p>
              <h1 className="page-heading">AI 回复质量</h1>
              <p className="page-subtitle">
                分开检查问题有没有找对、知识有没有找准，以及最终回复有没有忠于事实。
              </p>
            </div>
          </div>
        </header>
        <div className="empty-state py-20">
          <FileSearch className="mb-2 h-9 w-9 text-slate-400" />
          <p className="text-base font-bold text-slate-800">还没有评测报告</p>
          <p className="max-w-lg text-xs leading-5 text-slate-500">
            在 ECS 启动按需评测容器后，报告会自动出现在这里。页面只读取结果，不会在后台直接启动容器或调用付费模型。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="page-stack">
      <header className="page-header">
        <div className="page-header-row">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <p className="section-label">固定题库验收</p>
              {summary && (
                <span className={`status-pill ${summary.status === "completed" ? "pill-ok" : summary.status === "running" ? "pill-info" : "pill-warn"}`}>
                  {summary.status === "running" && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
                  {STATUS_LABELS[summary.status]}
                </span>
              )}
              {summary && (
                <span className={`status-pill ${summary.run_mode === "real" ? "pill-info" : summary.run_mode === "mock" ? "pill-warn" : "pill-neutral"}`}>
                  {RUN_MODE_LABELS[summary.run_mode]}
                </span>
              )}
            </div>
            <h1 className="page-heading">AI 回复质量</h1>
            <p className="page-subtitle">
              先看问题有没有理解对、知识有没有找准，再用经过中文校准的指标判断最终回复是否忠于事实。
            </p>
          </div>
          <label className="min-w-0 sm:w-80">
            <span className="mb-1.5 block text-xs font-bold text-slate-500">评测批次</span>
            <select
              value={selectedRunId}
              onChange={(event) => {
                setDetail(null);
                setSelectedRunId(event.target.value);
                setExpandedCase(null);
              }}
              className="min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-500/15"
            >
              {runs.map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {formatChinaShortDateTime(run.started_at)} · {STATUS_LABELS[run.status]}
                </option>
              ))}
            </select>
          </label>
        </div>
        {summary && (
          <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 border-t border-slate-100 pt-4 text-xs text-slate-500">
            <span>数据集 {summary.dataset_id} v{summary.dataset_version}</span>
            <span>运行类型 {RUN_MODE_LABELS[summary.run_mode]}</span>
            <span>模型 {summary.model}</span>
            <span>RAGAS {summary.ragas_version}</span>
            <span>代码 {summary.git_sha.slice(0, 10)}</span>
            <span>{summary.counts.completed}/{summary.counts.total} 条已完成</span>
          </div>
        )}
      </header>

      {isMockRun && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="flex items-start gap-2">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
            <p className="leading-6">
              <span className="font-bold">当前是 Mock 结构验收批次。</span>
              实际改写、召回、回复和得分由评测脚本模拟，仅用于检查链路与页面，不能代表真实模型质量。
            </p>
          </div>
        </div>
      )}

      {isLegacyRun && (
        <div className="rounded-xl border border-slate-300 bg-slate-100 px-4 py-3 text-sm text-slate-700">
          这份旧报告没有记录运行类型，页面不会根据模型名称猜测它是模拟还是真实评测。
        </div>
      )}

      <MetricCalibrationPanel
        answerRelevancy={initialCalibration}
        faithfulness={initialFaithfulnessCalibration}
      />

      {loadError && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
          <div className="flex items-center gap-2 font-bold"><AlertOctagon className="h-5 w-5" />报告读取失败</div>
          <p className="mt-1 text-xs leading-5">{loadError}</p>
        </div>
      )}

      {detail?.cases.length ? (
        <section className="grid overflow-hidden rounded-2xl border border-slate-200 bg-white sm:grid-cols-2 lg:grid-cols-4" aria-label="固定题库构成">
          {[
            ["FAQ 改写", originCounts.faq, "依据权威答案改成真实问法"],
            ["真实聊天", originCounts.chat, "一次性筛选并完成脱敏"],
            ["应该检索", originCounts.retrieve, "进入改写、召回和回复评测"],
            ["应该跳过", originCounts.skip, "检查误检与接话方式"],
          ].map(([label, value, hint], index) => (
            <div key={label} className={`px-5 py-4 ${index ? "border-t border-slate-100 sm:border-l sm:border-t-0" : ""}`}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-xs font-extrabold text-slate-600">{label}</span>
                <span className="text-2xl font-black tabular-nums text-slate-950">{value}</span>
              </div>
              <p className="mt-1 text-[11px] leading-5 text-slate-400">{hint}</p>
            </div>
          ))}
        </section>
      ) : null}

      {summary && summary.critical_fact_errors > 0 && (
        <section className="overflow-hidden rounded-2xl border border-rose-300 bg-rose-950 text-white shadow-sm">
          <div className="flex flex-col gap-4 px-5 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-rose-400/15 text-rose-300 ring-1 ring-inset ring-rose-300/20">
                <ShieldAlert className="h-5 w-5" />
              </span>
              <div>
                <h2 className="text-base font-extrabold">历史独立判官告警</h2>
                <p className="mt-1 text-sm leading-6 text-rose-100/75">
                  该批次曾额外检查数字、单位、否定和比较关系；新批次已统一由忠实度评价，不再重复判罚。
                </p>
              </div>
            </div>
            <div className="text-left sm:text-right">
              <div className="text-3xl font-black tabular-nums text-rose-200">{summary.critical_fact_errors}</div>
              <div className="text-xs font-semibold text-rose-200/70">次红色告警</div>
            </div>
          </div>
        </section>
      )}

      {loading && !detail ? (
        <div className="panel flex min-h-64 items-center justify-center text-sm font-semibold text-slate-500">
          <LoaderCircle className="mr-2 h-5 w-5 animate-spin text-cyan-600" />正在读取评测明细…
        </div>
      ) : summary ? (
        <>
          <div className="grid min-w-0 gap-5 lg:grid-cols-2">
            <section className="panel min-w-0">
              <div className="panel-header-clean">
                <div className="flex items-center gap-2">
                  <SearchCheck className="h-5 w-5 text-cyan-700" />
                  <h2 className="panel-title">问题处理</h2>
                </div>
                <p className="panel-subtitle">先判断该不该查，再看改写有没有让召回变差。</p>
              </div>
              <div className="px-5 sm:px-6">
                <MetricLine label="检索决策错误率" hint="该查却没查，或不该查却发起了检索；目标为 0。" metric={summary.metrics.retrieval_decision_error_rate} lowerIsBetter />
                <MetricLine label="改写伤害率" hint="改写后的事实召回率比原问题低超过 5 个百分点；目标为 0。" metric={summary.metrics.rewrite_harm_rate} lowerIsBetter />
              </div>
            </section>

            <section className="panel min-w-0">
              <div className="panel-header-clean">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-5 w-5 text-cyan-700" />
                  <h2 className="panel-title">RAGAS 四项{isMockRun ? "（模拟）" : ""}</h2>
                </div>
                <p className="panel-subtitle">
                  {summary.answer_relevancy_policy.enforced && summary.faithfulness_policy.enforced
                    ? "四项都保留，各自回答不同问题，不合并成一个总分。"
                    : "四项分数都保留；未通过中文校准的指标只观察，不参与本批次通过判定。"}
                </p>
              </div>
              <div className="grid px-5 sm:grid-cols-2 sm:gap-x-6 sm:px-6">
                <MetricLine label="事实召回率" hint="该找到的权威事实找全了吗？" metric={summary.metrics.context_recall} threshold={0.8} />
                <MetricLine label="召回精确率" hint="找回来的内容是否真正有用？" metric={summary.metrics.context_precision} threshold={0.7} />
                <MetricLine
                  label="回复忠实度"
                  hint={summary.faithfulness_policy.enforced ? "最终回复是否脱离或反转召回事实？" : summary.faithfulness_policy.reason}
                  metric={summary.metrics.faithfulness}
                  threshold={summary.faithfulness_policy.threshold ?? undefined}
                  observational={!summary.faithfulness_policy.enforced}
                />
                <MetricLine
                  label="回复相关性"
                  hint={summary.answer_relevancy_policy.enforced ? "有没有正面回答用户当前问题？" : summary.answer_relevancy_policy.reason}
                  metric={summary.metrics.answer_relevancy}
                  threshold={summary.answer_relevancy_policy.threshold ?? undefined}
                  observational={!summary.answer_relevancy_policy.enforced}
                />
              </div>
            </section>
          </div>

          <section className="panel overflow-hidden">
            <div className="panel-header-clean">
              <div className="toolbar-row">
                <div>
                  <div className="flex items-center gap-2">
                    <FileSearch className="h-5 w-5 text-cyan-700" />
                    <h2 className="panel-title">评测用例</h2>
                  </div>
                  <p className="panel-subtitle">
                    本批次共 {summary.counts.total} 条；展开任意一条，对照测试问题、预期结果、{resultLabel}和最终判定。
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {([
                    ["all", `全部 ${summary.counts.total}`],
                    ["failed", `未通过 ${summary.counts.failed + summary.counts.errors}`],
                    ["passed", `通过 ${summary.counts.passed}`],
                    ["critical", `历史事实告警 ${summary.critical_fact_errors}`],
                    ["faq_rewrite", `FAQ 改写 ${originCounts.faq}`],
                    ["real_chat", `真实聊天 ${originCounts.chat}`],
                    ["decision", "检索决策异常"],
                    ["rewrite", "问题改写异常"],
                    ["retrieval", "知识召回异常"],
                    ["answer", "最终回复异常"],
                    ["ragas", "RAGAS 未达标"],
                  ] as const).map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setFilter(key)}
                      className={`min-h-9 rounded-lg border px-3 text-xs font-bold transition ${filter === key ? "border-cyan-300 bg-cyan-50 text-cyan-800" : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800"}`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {visibleCases.length ? (
              <div className="divide-y divide-slate-200">
                {visibleCases.map((item) => {
                  const expanded = expandedCase === item.case_id;
                  const critical = item.failure_reasons.some((reason) => reason.severity === "critical");
                  const actualDecision = item.actual.retrieval_decision;
                  return (
                    <article key={item.case_id}>
                      <button
                        type="button"
                        onClick={() => setExpandedCase(expanded ? null : item.case_id)}
                        aria-expanded={expanded}
                        className="flex w-full min-w-0 items-center gap-3 px-4 py-4 text-left transition hover:bg-slate-50 sm:px-6"
                      >
                        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${critical ? "bg-rose-100 text-rose-700" : item.status === "error" ? "bg-amber-100 text-amber-700" : item.status === "passed" ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>
                          {critical ? <AlertOctagon className="h-4.5 w-4.5" /> : item.status === "error" ? <CircleDashed className="h-4.5 w-4.5" /> : item.status === "passed" ? <CheckCircle2 className="h-4.5 w-4.5" /> : <TriangleAlert className="h-4.5 w-4.5" />}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-bold leading-6 text-slate-950">{item.user_query}</span>
                            <CaseStatus status={item.status} />
                          </span>
                          <span className="mt-1 block text-xs leading-5 text-slate-500">{item.title}</span>
                          <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] font-semibold text-slate-500">
                            <span className={item.origin.kind === "real_chat" ? "text-violet-700" : "text-cyan-700"}>
                              {ORIGIN_LABELS[item.origin.kind]}
                            </span>
                            <span className="text-slate-300">·</span>
                            <span>预期：{DECISION_LABELS[item.expected.retrieval_decision]}</span>
                            <ArrowDown className="h-3 w-3 -rotate-90 text-slate-300" />
                            <span className={actualDecision === item.expected.retrieval_decision ? "text-emerald-700" : "text-rose-700"}>
                              {isMockRun ? "模拟" : "实际"}：{actualDecision ? DECISION_LABELS[actualDecision] : "无结果"}
                            </span>
                          </span>
                        </span>
                        <span className="hidden shrink-0 items-center gap-1 text-xs font-semibold text-slate-400 sm:flex">
                          <Clock3 className="h-3.5 w-3.5" /> {item.elapsed_ms ?? 0}ms
                        </span>
                        <ChevronRight className={`h-5 w-5 shrink-0 text-slate-400 transition ${expanded ? "rotate-90" : ""}`} />
                      </button>
                      {expanded && (
                        <EvidenceDetail
                          item={item}
                          runMode={summary.run_mode}
                          answerRelevancyEnforced={summary.answer_relevancy_policy.enforced}
                          faithfulnessEnforced={summary.faithfulness_policy.enforced}
                        />
                      )}
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="empty-state m-5 sm:m-6">
                <CheckCircle2 className="h-8 w-8 text-emerald-600" />
                <p className="font-bold text-slate-800">当前筛选下没有评测用例</p>
                <p className="text-xs text-slate-500">本批次通过 {summary.counts.passed} 条，失败 {summary.counts.failed} 条，运行错误 {summary.counts.errors} 条。</p>
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
