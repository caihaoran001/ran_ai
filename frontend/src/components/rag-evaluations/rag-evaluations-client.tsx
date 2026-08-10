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

export type RagEvaluationSummary = {
  schema_version: 1;
  run_id: string;
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

type FailureReason = {
  stage: "decision" | "rewrite" | "retrieval" | "answer" | "ragas" | "runtime";
  code: string;
  message: string;
  severity: "info" | "warning" | "critical";
};

type EvaluationCase = {
  case_id: string;
  title: string;
  tags: string[];
  status: "passed" | "failed" | "skipped" | "error";
  history: Array<{ role: "user" | "staff" | "assistant"; content: string }>;
  user_query: string;
  expected: {
    retrieval_decision: "retrieve" | "skip";
    required_facts: string[];
    forbidden_claims: string[];
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
  failure_reasons: FailureReason[];
  elapsed_ms?: number | null;
};

type RagEvaluationRunDetail = {
  summary: RagEvaluationSummary;
  cases: EvaluationCase[];
};

export type RagEvaluationRunList = { items: RagEvaluationSummary[] };

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

type CaseFilter =
  | "all"
  | "passed"
  | "failed"
  | "critical"
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
}: {
  label: string;
  hint: string;
  metric: MetricValue;
  lowerIsBetter?: boolean;
  threshold?: number;
}) {
  const value = metric.value ?? null;
  const healthy =
    value == null
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
          className={`h-full rounded-full ${healthy === false ? "bg-rose-500" : lowerIsBetter ? "bg-cyan-500" : "bg-emerald-500"}`}
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
  tone = "neutral",
}: {
  title: string;
  items: string[];
  tone?: "neutral" | "danger";
}) {
  if (!items.length) return null;
  return (
    <div>
      <p className={`text-xs font-bold ${tone === "danger" ? "text-rose-700" : "text-slate-600"}`}>
        {title}
      </p>
      <ul className="mt-1.5 space-y-1.5">
        {items.map((fact, index) => (
          <li key={`${fact}-${index}`} className="flex gap-2 text-sm leading-6 text-slate-700">
            <span className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${tone === "danger" ? "bg-rose-500" : "bg-cyan-600"}`} />
            <span>{fact}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EvidenceDetail({ item }: { item: EvaluationCase }) {
  const hits = item.actual.rewritten_query_hits;
  const actualDecision = item.actual.retrieval_decision;
  const decisionMatched = actualDecision === item.expected.retrieval_decision;
  const scoreItems = [
    ["事实召回", item.scores.context_recall],
    ["召回精确", item.scores.context_precision],
    ["回复忠实", item.scores.faithfulness],
    ["回复相关", item.scores.answer_relevancy],
  ] as const;

  return (
    <div className="min-w-0 border-t border-slate-200 bg-slate-50/70 px-4 py-5 sm:px-6">
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white md:grid md:grid-cols-2 md:divide-x md:divide-slate-200">
        <section className="min-w-0 border-b border-slate-200 p-4 sm:p-5 md:border-b-0" aria-label="预期结果">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h3 className="text-sm font-extrabold text-slate-950">预期结果</h3>
            <span className="status-pill pill-info">
              {DECISION_LABELS[item.expected.retrieval_decision]}
            </span>
          </div>
          <div className="space-y-4">
            <FactList title="回答必须依据" items={item.expected.required_facts} />
            <FactList title="回答不得出现" items={item.expected.forbidden_claims} tone="danger" />
            {!item.expected.required_facts.length && !item.expected.forbidden_claims.length && (
              <p className="text-sm leading-6 text-slate-500">
                本条只验证系统是否正确跳过知识检索，不预设知识型答案。
              </p>
            )}
          </div>
        </section>

        <section className="min-w-0 p-4 sm:p-5" aria-label="实际结果">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-extrabold text-slate-950">实际结果</h3>
            <span className={`status-pill ${decisionMatched ? "pill-ok" : "pill-risk"}`}>
              {actualDecision ? DECISION_LABELS[actualDecision] : "未产出检索决策"}
            </span>
          </div>
          <dl className="space-y-4">
            <div>
              <dt className="text-xs font-bold text-slate-500">实际改写问题</dt>
              <dd className="mt-1 break-words text-sm font-semibold leading-6 text-slate-800">
                {item.actual.knowledge_query || "未生成知识查询"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-bold text-slate-500">实际回复</dt>
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
          <div className="mb-3 flex items-center gap-2 text-xs font-bold text-slate-600">
            <Database className="h-4 w-4 text-slate-500" /> 实际召回内容
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
                没有召回内容
              </p>
            )}
          </div>
        </section>
      </div>

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
                实际结果符合本条预期，未发现失败原因。
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
export function RagEvaluationsClient({
  initialRuns,
}: {
  initialRuns: RagEvaluationSummary[];
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
    return cases.filter((item) =>
      item.failure_reasons.some((reason) => reason.stage === filter),
    );
  }, [detail, filter]);
  const isMockRun = summary?.model.toLowerCase().includes("mock") ?? false;

  if (!initialRuns.length) {
    return (
      <div className="page-stack">
        <header className="page-header">
          <div className="page-header-row">
            <div>
              <p className="section-label">AI 质量控制</p>
              <h1 className="page-heading">RAG 评测</h1>
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
              <p className="section-label">AI 质量控制</p>
              {summary && (
                <span className={`status-pill ${summary.status === "completed" ? "pill-ok" : summary.status === "running" ? "pill-info" : "pill-warn"}`}>
                  {summary.status === "running" && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
                  {STATUS_LABELS[summary.status]}
                </span>
              )}
            </div>
            <h1 className="page-heading">RAG 评测</h1>
            <p className="page-subtitle">
              检索决策和改写伤害单独看，四项 RAGAS 评价召回与回复；数字、单位、否定和比较反转另外记红色告警。
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

      {loadError && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
          <div className="flex items-center gap-2 font-bold"><AlertOctagon className="h-5 w-5" />报告读取失败</div>
          <p className="mt-1 text-xs leading-5">{loadError}</p>
        </div>
      )}

      {summary && summary.critical_fact_errors > 0 && (
        <section className="overflow-hidden rounded-2xl border border-rose-300 bg-rose-950 text-white shadow-sm">
          <div className="flex flex-col gap-4 px-5 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-rose-400/15 text-rose-300 ring-1 ring-inset ring-rose-300/20">
                <ShieldAlert className="h-5 w-5" />
              </span>
              <div>
                <h2 className="text-base font-extrabold">发现关键事实错误</h2>
                <p className="mt-1 text-sm leading-6 text-rose-100/75">
                  涉及数字、单位、否定或比较方向的回复错误，不会被平均分抵消。
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
                  <h2 className="panel-title">RAGAS 四项</h2>
                </div>
                <p className="panel-subtitle">四项都保留，各自回答不同问题，不合并成一个总分。</p>
              </div>
              <div className="grid px-5 sm:grid-cols-2 sm:gap-x-6 sm:px-6">
                <MetricLine label="事实召回率" hint="该找到的权威事实找全了吗？" metric={summary.metrics.context_recall} threshold={0.8} />
                <MetricLine label="召回精确率" hint="找回来的内容是否真正有用？" metric={summary.metrics.context_precision} threshold={0.7} />
                <MetricLine label="回复忠实度" hint="最终回复是否脱离或反转召回事实？" metric={summary.metrics.faithfulness} threshold={0.85} />
                <MetricLine label="回复相关性" hint="有没有正面回答用户当前问题？" metric={summary.metrics.answer_relevancy} threshold={0.8} />
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
                    本批次共 {summary.counts.total} 条；展开任意一条，对照测试问题、预期结果、实际结果和最终判定。
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {([
                    ["all", `全部 ${summary.counts.total}`],
                    ["failed", `未通过 ${summary.counts.failed + summary.counts.errors}`],
                    ["passed", `通过 ${summary.counts.passed}`],
                    ["critical", `关键事实 ${summary.critical_fact_errors}`],
                    ["decision", "是否检索"],
                    ["rewrite", "问题改写"],
                    ["retrieval", "知识召回"],
                    ["answer", "最终回复"],
                    ["ragas", "RAGAS"],
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
                            <span>预期：{DECISION_LABELS[item.expected.retrieval_decision]}</span>
                            <ArrowDown className="h-3 w-3 -rotate-90 text-slate-300" />
                            <span className={actualDecision === item.expected.retrieval_decision ? "text-emerald-700" : "text-rose-700"}>
                              实际：{actualDecision ? DECISION_LABELS[actualDecision] : "无结果"}
                            </span>
                          </span>
                        </span>
                        <span className="hidden shrink-0 items-center gap-1 text-xs font-semibold text-slate-400 sm:flex">
                          <Clock3 className="h-3.5 w-3.5" /> {item.elapsed_ms ?? 0}ms
                        </span>
                        <ChevronRight className={`h-5 w-5 shrink-0 text-slate-400 transition ${expanded ? "rotate-90" : ""}`} />
                      </button>
                      {expanded && <EvidenceDetail item={item} />}
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
