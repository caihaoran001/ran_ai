"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertOctagon,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Clock3,
  Coins,
  FileCheck2,
  Gauge,
  LoaderCircle,
  MessageSquareText,
  ShieldCheck,
  TriangleAlert,
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

type AgentJudgePolicy = {
  status: "mock" | "observational" | "enforced";
  enforced: boolean;
  model: string;
  reason: string;
};

type EvaluationUsageTotals = {
  call_count: number;
  successful_calls: number;
  failed_calls: number;
  usage_reported_calls: number;
  costed_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  prompt_cache_hit_tokens: number;
  known_estimated_cost_yuan: number;
  estimated_cost_yuan?: number | null;
  complete: boolean;
};

type EvaluationModelCall = {
  component: "agent" | "judge";
  metric_key?: string | null;
  call_index: number;
  model: string;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  prompt_cache_hit_tokens?: number | null;
  estimated_cost_yuan?: number | null;
  elapsed_ms: number;
  success: boolean;
  error_type?: string | null;
};

type EvaluationUsage = {
  tracking_available: boolean;
  currency: "CNY";
  is_estimate: true;
  agent: EvaluationUsageTotals;
  judge: EvaluationUsageTotals;
  total: EvaluationUsageTotals;
};

export type AgentEvaluationSummary = {
  schema_version: 1;
  run_id: string;
  run_mode: "mock" | "real";
  status: "running" | "completed" | "failed" | "interrupted";
  started_at: string;
  updated_at: string;
  finished_at?: string | null;
  git_sha: string;
  dataset_id: string;
  dataset_version: number;
  repeat_count: number;
  model: string;
  evaluation_framework?: "legacy_custom" | "deepeval";
  evaluation_framework_version?: string | null;
  judge_policy: AgentJudgePolicy;
  counts: {
    total: number;
    completed: number;
    passed: number;
    failed: number;
    errors: number;
  };
  metrics: {
    task_pass_rate: RateMetric;
    tool_selection_accuracy: RateMetric;
    argument_accuracy: RateMetric;
    unauthorized_action_rate: RateMetric;
    literal_constraint_pass_rate: RateMetric;
    judge_pass_rate: RateMetric;
    judge_task_completion_rate: RateMetric;
    judge_groundedness_rate: RateMetric;
    judge_clarification_rate: RateMetric;
    judge_expression_rate: RateMetric;
  };
  usage?: EvaluationUsage;
  estimated_cost_yuan?: number | null;
  error?: string | null;
};

type ExpectedToolCall = {
  name: string;
  args_subset: Record<string, unknown>;
  string_args: Record<string, {
    required_literals: string[];
    required_any_groups: string[][];
    forbidden_literals: string[];
  }>;
  min_calls: number;
  max_calls: number;
};

type ExpectedResult = {
  response_mode: "reply" | "clarify" | "no_reply";
  required_tools: ExpectedToolCall[];
  forbidden_tools: string[];
  allowed_additional_tools: string[];
  allow_additional_tools: boolean;
  required_literals: string[];
  forbidden_literals: string[];
  required_facts: string[];
  forbidden_claims: string[];
  clarification_requirement?: string | null;
};

type ToolCall = {
  name: string;
  args: Record<string, unknown>;
  result: unknown;
  ok: boolean;
  error?: string | null;
  elapsed_ms: number;
};

type DeterministicCheck = {
  code: string;
  label: string;
  passed: boolean;
  message: string;
  severity: "info" | "warning" | "critical";
};

type JudgeDimension = {
  key: "task_completion" | "groundedness" | "clarification" | "expression";
  passed?: boolean | null;
  reason: string;
  evidence?: string | null;
};

type FrameworkMetric = {
  key: string;
  label: string;
  kind: "deterministic" | "llm";
  score?: number | null;
  threshold?: number | null;
  passed?: boolean | null;
  reason?: string | null;
};

type JudgeResult = {
  status: "mock" | "completed" | "skipped" | "error";
  passed?: boolean | null;
  dimensions: JudgeDimension[];
  framework_metrics?: FrameworkMetric[];
  error?: string | null;
};

type FailureReason = {
  stage: "deterministic" | "judge" | "runtime";
  code: string;
  message: string;
  severity: "warning" | "critical";
};

type AgentEvaluationCase = {
  case_id: string;
  title: string;
  origin: "production_derived" | "boundary";
  scenario: string;
  tags: string[];
  round_index: number;
  status: "passed" | "failed" | "error";
  history: Array<{ role: "user" | "staff" | "assistant"; content: string }>;
  user_query: string;
  expected: ExpectedResult;
  actual: {
    response_mode: "reply" | "no_reply" | "unavailable";
    response?: string | null;
    tool_calls: ToolCall[];
  };
  deterministic_checks: DeterministicCheck[];
  judge: JudgeResult;
  failure_reasons: FailureReason[];
  usage?: EvaluationUsage & { calls: EvaluationModelCall[] };
  elapsed_ms?: number | null;
};

type AgentEvaluationRunDetail = {
  summary: AgentEvaluationSummary;
  cases: AgentEvaluationCase[];
};

export type AgentEvaluationRunList = { items: AgentEvaluationSummary[] };

type CaseFilter =
  | "all"
  | "failed"
  | "passed"
  | "error"
  | "production_derived"
  | "boundary"
  | "deterministic"
  | "judge";

const STATUS_LABELS: Record<AgentEvaluationSummary["status"], string> = {
  running: "正在评测",
  completed: "评测完成",
  failed: "运行失败",
  interrupted: "运行中断",
};

const RESPONSE_MODE_LABELS: Record<ExpectedResult["response_mode"], string> = {
  reply: "直接回复",
  clarify: "追问必要信息",
  no_reply: "不回复",
};

const JUDGE_LABELS: Record<JudgeDimension["key"], string> = {
  task_completion: "任务完成",
  groundedness: "证据忠实",
  clarification: "澄清合理",
  expression: "表达质量",
};

const ROLE_LABELS = { user: "用户", staff: "工作人员", assistant: "AI助教" };

const COST_METRIC_LABELS: Record<string, string> = {
  tool_correctness: "工具调用精确匹配",
  task_completion: "任务完成",
  groundedness: "证据忠实",
  clarification: "澄清合理",
  expression: "表达质量",
};

function frameworkLabel(summary: AgentEvaluationSummary) {
  if (summary.evaluation_framework === "deepeval") {
    return `DeepEval${summary.evaluation_framework_version ? ` ${summary.evaluation_framework_version}` : ""}`;
  }
  return "历史自研判断";
}

function percent(value?: number | null) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function formatTokens(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatYuan(value?: number | null) {
  if (value == null) return "—";
  const digits = value === 0 ? 4 : value < 0.01 ? 6 : 4;
  return `¥${value.toFixed(digits)}`;
}

function UsageCostCard({
  label,
  hint,
  usage,
  accent = false,
}: {
  label: string;
  hint: string;
  usage: EvaluationUsageTotals;
  accent?: boolean;
}) {
  return (
    <div className={`min-w-0 px-5 py-4 sm:px-6 ${accent ? "bg-cyan-50/60" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-extrabold text-slate-600">{label}</p>
          <p className="mt-1 text-[11px] leading-5 text-slate-400">{hint}</p>
        </div>
        {!usage.complete && <span className="status-pill pill-warn">不完整</span>}
      </div>
      <p className={`mt-3 text-2xl font-black tabular-nums ${accent ? "text-cyan-900" : "text-slate-950"}`}>
        {usage.complete
          ? formatYuan(usage.estimated_cost_yuan)
          : `已知 ${formatYuan(usage.known_estimated_cost_yuan)}`}
      </p>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] tabular-nums text-slate-500">
        <span>{usage.call_count} 次调用</span>
        <span>{formatTokens(usage.total_tokens)} Token</span>
        {usage.prompt_cache_hit_tokens > 0 && (
          <span>{formatTokens(usage.prompt_cache_hit_tokens)} 缓存命中</span>
        )}
      </div>
    </div>
  );
}

function EvaluationCostPanel({ usage }: { usage?: EvaluationUsage }) {
  if (!usage?.tracking_available) {
    return (
      <section className="panel px-5 py-5 sm:px-6">
        <div className="flex items-start gap-3">
          <Coins className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
          <div>
            <h2 className="panel-title">评测费用</h2>
            <p className="panel-subtitle">这是历史报告，当时还没有记录逐次模型调用和 Token。</p>
          </div>
        </div>
      </section>
    );
  }
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean">
        <div className="flex items-center gap-2">
          <Coins className="h-5 w-5 text-cyan-700" />
          <h2 className="panel-title">评测费用</h2>
        </div>
        <p className="panel-subtitle">
          按每次 API 返回的 Token 和运行时人民币单价估算；缺少 usage 的调用不会被冒充为完整总价。
        </p>
      </div>
      <div className="grid divide-y divide-slate-100 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <UsageCostCard label="Agent 生成" hint="完成题目时的思考与回复调用" usage={usage.agent} />
        <UsageCostCard label="DeepEval 判断" hint="工具与语义指标的判断官调用" usage={usage.judge} />
        <UsageCostCard label="本批合计" hint="生成成本与判断成本之和" usage={usage.total} accent />
      </div>
    </section>
  );
}

function CaseUsageDetail({ usage }: { usage?: AgentEvaluationCase["usage"] }) {
  if (!usage?.tracking_available) return null;
  return (
    <section className="mt-5 border-t border-slate-200 pt-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Coins className="h-4 w-4 text-cyan-700" />
          <h3 className="text-sm font-extrabold text-slate-950">模型调用与费用</h3>
        </div>
        <span className={`status-pill ${usage.total.complete ? "pill-neutral" : "pill-warn"}`}>
          {usage.total.complete ? "账本完整" : `${usage.total.costed_calls}/${usage.total.call_count} 次可计费`}
        </span>
      </div>
      <div className="mt-3 grid overflow-hidden rounded-xl border border-slate-200 bg-white sm:grid-cols-3 sm:divide-x sm:divide-slate-100">
        <UsageCostCard label="Agent" hint="生成回复" usage={usage.agent} />
        <UsageCostCard label="判断官" hint="DeepEval 指标" usage={usage.judge} />
        <UsageCostCard label="本题合计" hint="全部模型调用" usage={usage.total} accent />
      </div>
      {usage.calls.length ? (
        <div className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
          {usage.calls.map((call) => (
            <div key={call.call_index} className="flex min-w-0 flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`status-pill ${call.component === "agent" ? "pill-info" : "pill-neutral"}`}>
                    {call.component === "agent" ? "Agent" : "判断官"} #{call.call_index}
                  </span>
                  {call.metric_key && (
                    <span className="text-xs font-bold text-slate-700">
                      {COST_METRIC_LABELS[call.metric_key] ?? call.metric_key}
                    </span>
                  )}
                  {!call.success && <span className="status-pill pill-risk">调用失败</span>}
                </div>
                <p className="mt-1 truncate text-[11px] text-slate-400">{call.model} · {call.elapsed_ms}ms</p>
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] tabular-nums text-slate-500 sm:justify-end">
                <span>输入 {formatTokens(call.prompt_tokens ?? 0)}</span>
                <span>输出 {formatTokens(call.completion_tokens ?? 0)}</span>
                {Boolean(call.prompt_cache_hit_tokens) && <span>缓存 {formatTokens(call.prompt_cache_hit_tokens ?? 0)}</span>}
                <span className="font-extrabold text-slate-800">
                  {call.estimated_cost_yuan == null ? "费用未知" : formatYuan(call.estimated_cost_yuan)}
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 text-xs leading-5 text-slate-400">Mock 模式不调用模型，本题没有 API 费用。</p>
      )}
    </section>
  );
}

function JsonEvidence({ value }: { value: unknown }) {
  return (
    <pre className="text-break mt-2 whitespace-pre-wrap rounded-lg bg-slate-950 px-3 py-2.5 text-[11px] leading-5 text-slate-200">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function MetricItem({
  label,
  hint,
  metric,
  lowerIsBetter = false,
  observational = false,
  threshold = 1,
}: {
  label: string;
  hint: string;
  metric: RateMetric;
  lowerIsBetter?: boolean;
  observational?: boolean;
  threshold?: number;
}) {
  const value = metric.value ?? null;
  const healthy = value == null || observational
    ? null
    : lowerIsBetter
      ? value === 0
      : value >= threshold;
  const width = value == null ? 0 : Math.max(2, Math.min(100, value * 100));
  return (
    <div className="min-w-0 border-t border-slate-100 px-5 py-4 first:border-t-0 sm:px-6 sm:[&:nth-child(-n+2)]:border-t-0 lg:[&:nth-child(-n+5)]:border-t-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-extrabold text-slate-900">{label}</p>
            {observational && <span className="status-pill pill-neutral">仅观察</span>}
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-500">{hint}</p>
        </div>
        <div className={`shrink-0 text-xl font-black tabular-nums ${healthy === false ? "text-rose-700" : "text-slate-950"}`}>
          {percent(value)}
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${observational ? "bg-slate-400" : healthy === false ? "bg-rose-500" : lowerIsBetter ? "bg-cyan-500" : "bg-emerald-500"}`}
          style={{ width: `${width}%` }}
        />
      </div>
      <p className="mt-2 text-[11px] tabular-nums text-slate-400">
        {metric.numerator} / {metric.denominator} 次
      </p>
    </div>
  );
}

function CaseStatus({ status }: { status: AgentEvaluationCase["status"] }) {
  const labels = { passed: "通过", failed: "未通过", error: "运行错误" };
  const styles = { passed: "pill-ok", failed: "pill-risk", error: "pill-warn" };
  return <span className={`status-pill ${styles[status]}`}>{labels[status]}</span>;
}

function EvidenceDetail({
  item,
  judgeObservational,
}: {
  item: AgentEvaluationCase;
  judgeObservational: boolean;
}) {
  return (
    <div className="min-w-0 border-t border-slate-200 bg-slate-50/70 px-4 py-5 sm:px-6">
      <section className="grid min-w-0 gap-5 lg:grid-cols-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-extrabold text-slate-950">题目与预期</h3>
            <span className="status-pill pill-info">{RESPONSE_MODE_LABELS[item.expected.response_mode]}</span>
          </div>
          {item.history.length > 0 && (
            <div className="mt-4 border-l-2 border-slate-200 pl-3">
              <p className="mb-2 text-xs font-bold text-slate-500">前序对话</p>
              <div className="space-y-2">
                {item.history.map((message, index) => (
                  <p key={`${message.role}-${index}`} className="text-sm leading-6 text-slate-700">
                    <span className="font-bold text-slate-900">{ROLE_LABELS[message.role]}：</span>
                    {message.content}
                  </p>
                ))}
              </div>
            </div>
          )}
          <p className="mt-4 rounded-lg bg-slate-200/70 px-3 py-2.5 text-sm font-bold leading-6 text-slate-950">
            {item.user_query}
          </p>
          <div className="mt-4 space-y-3 text-xs leading-5 text-slate-600">
            <div>
              <p className="font-extrabold text-slate-800">必须调用</p>
              {item.expected.required_tools.length ? item.expected.required_tools.map((tool) => (
                <div key={tool.name} className="mt-2 border-l-2 border-cyan-300 pl-3">
                  <code className="font-bold text-cyan-800">{tool.name}</code>
                  <JsonEvidence value={tool.args_subset} />
                  {Object.keys(tool.string_args).length > 0 && (
                    <>
                      <p className="mt-2 text-[11px] font-bold text-slate-500">文本参数规则</p>
                      <JsonEvidence value={tool.string_args} />
                    </>
                  )}
                </div>
              )) : <p className="mt-1 text-slate-400">本题不应依赖工具。</p>}
            </div>
            {item.expected.forbidden_tools.length > 0 && (
              <p><span className="font-extrabold text-slate-800">禁止调用：</span>{item.expected.forbidden_tools.join("、")}</p>
            )}
            {item.expected.allowed_additional_tools.length > 0 && (
              <p><span className="font-extrabold text-slate-800">可选辅助：</span>{item.expected.allowed_additional_tools.join("、")}</p>
            )}
            {item.expected.required_facts.length > 0 && (
              <div>
                <p className="font-extrabold text-slate-800">回复必须表达</p>
                <ul className="mt-1 list-disc space-y-1 pl-5">
                  {item.expected.required_facts.map((fact) => <li key={fact}>{fact}</li>)}
                </ul>
              </div>
            )}
            {item.expected.clarification_requirement && (
              <p><span className="font-extrabold text-slate-800">澄清要求：</span>{item.expected.clarification_requirement}</p>
            )}
          </div>
        </div>

        <div className="min-w-0 border-t border-slate-200 pt-5 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-extrabold text-slate-950">实际执行证据</h3>
            <span className="status-pill pill-neutral">第 {item.round_index} 轮</span>
          </div>
          <div className="mt-4 space-y-3">
            {item.actual.tool_calls.length ? item.actual.tool_calls.map((call, index) => (
              <div key={`${call.name}-${index}`} className="border-l-2 border-slate-300 pl-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <code className="text-xs font-extrabold text-slate-900">{call.name}</code>
                  <span className={`status-pill ${call.ok ? "pill-ok" : "pill-risk"}`}>
                    {call.ok ? "工具成功" : "工具失败"} · {call.elapsed_ms}ms
                  </span>
                </div>
                <p className="mt-2 text-[11px] font-bold text-slate-500">调用参数</p>
                <JsonEvidence value={call.args} />
                <p className="mt-2 text-[11px] font-bold text-slate-500">隔离环境返回</p>
                <JsonEvidence value={call.result} />
              </div>
            )) : (
              <p className="text-xs leading-5 text-slate-400">本轮没有工具调用。</p>
            )}
          </div>
          <div className="mt-5 border-t border-slate-200 pt-4">
            <p className="text-xs font-extrabold text-slate-700">最终回复</p>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-800">
              {item.actual.response || "没有产出回复。"}
            </p>
          </div>
        </div>
      </section>

      <CaseUsageDetail usage={item.usage} />

      <section className="mt-5 grid min-w-0 gap-5 border-t border-slate-200 pt-5 lg:grid-cols-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-cyan-700" />
            <h3 className="text-sm font-extrabold text-slate-950">代码硬检查</h3>
          </div>
          <div className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
            {item.deterministic_checks.length ? item.deterministic_checks.map((check) => (
              <div key={check.code} className="flex items-start gap-2 py-3">
                {check.passed
                  ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                  : <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" />}
                <div className="min-w-0">
                  <p className="text-xs font-extrabold text-slate-800">{check.label}</p>
                  <p className="mt-0.5 text-xs leading-5 text-slate-500">{check.message}</p>
                </div>
              </div>
            )) : <p className="py-3 text-xs text-slate-400">运行失败，未完成代码检查。</p>}
          </div>
        </div>

        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Gauge className="h-4 w-4 text-cyan-700" />
            <h3 className="text-sm font-extrabold text-slate-950">
              {item.judge.framework_metrics?.length ? "DeepEval 判断官" : "LLM 判断官"}
            </h3>
            {judgeObservational && <span className="status-pill pill-neutral">仅观察</span>}
          </div>
          {item.judge.error && (
            <p className="mt-3 text-xs leading-5 text-rose-700">{item.judge.error}</p>
          )}
          <div className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
            {item.judge.dimensions.length ? item.judge.dimensions.map((dimension) => (
              <div key={dimension.key} className="py-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs font-extrabold text-slate-800">{JUDGE_LABELS[dimension.key]}</p>
                  <span className={`status-pill ${dimension.passed === true ? "pill-ok" : dimension.passed === false ? "pill-risk" : "pill-neutral"}`}>
                    {dimension.passed === true ? "通过" : dimension.passed === false ? "未通过" : "无结论"}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-500">{dimension.reason}</p>
                {dimension.evidence && (
                  <p className="mt-1 border-l-2 border-slate-300 pl-2 text-[11px] leading-5 text-slate-500">
                    证据：{dimension.evidence}
                  </p>
                )}
              </div>
            )) : <p className="py-3 text-xs text-slate-400">本轮没有判断官结果。</p>}
          </div>
          {Boolean(item.judge.framework_metrics?.length) && (
            <div className="mt-4 rounded-xl border border-slate-200 bg-white px-3 py-3">
              <p className="text-[11px] font-extrabold tracking-wide text-slate-500">DeepEval 原始指标</p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {item.judge.framework_metrics?.map((metric) => (
                  <div key={metric.key} className="min-w-0 rounded-lg bg-slate-50 px-3 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-bold text-slate-800">{metric.label}</p>
                        <p className="mt-0.5 text-[10px] text-slate-400">
                          {metric.kind === "deterministic" ? "确定性指标" : "LLM 指标"}
                        </p>
                      </div>
                      <span className={`status-pill ${metric.passed === true ? "pill-ok" : metric.passed === false ? "pill-risk" : "pill-neutral"}`}>
                        {percent(metric.score)}
                      </span>
                    </div>
                    {metric.key === "tool_correctness" && metric.reason && (
                      <p className="mt-2 text-[10px] leading-4 text-slate-500">{metric.reason}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      {item.failure_reasons.length > 0 && (
        <section className="mt-5 border-t border-rose-200 pt-4">
          <div className="flex items-center gap-2 text-sm font-extrabold text-rose-800">
            <AlertOctagon className="h-4 w-4" />失败原因
          </div>
          <ul className="mt-2 space-y-1.5">
            {item.failure_reasons.map((reason, index) => (
              <li key={`${reason.code}-${index}`} className="text-xs leading-5 text-rose-700">
                {reason.message}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function AgentEvaluationsClient({
  initialRuns,
}: {
  initialRuns: AgentEvaluationSummary[];
}) {
  const [runs, setRuns] = useState(initialRuns);
  const [selectedRunId, setSelectedRunId] = useState(initialRuns[0]?.run_id ?? "");
  const [detail, setDetail] = useState<AgentEvaluationRunDetail | null>(null);
  const [loading, setLoading] = useState(Boolean(initialRuns.length));
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<CaseFilter>("all");
  const [expandedCase, setExpandedCase] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    api<AgentEvaluationRunDetail>(`/agent-evaluations/${selectedRunId}`)
      .then((payload) => {
        if (cancelled) return;
        setDetail(payload);
        setRuns((current) => current.map((item) =>
          item.run_id === payload.summary.run_id ? payload.summary : item,
        ));
        const first = payload.cases.find((item) => item.status !== "passed") ?? payload.cases[0];
        setExpandedCase(first ? `${first.case_id}-${first.round_index}` : null);
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : "报告读取失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [selectedRunId]);

  const summary = detail?.summary ?? runs.find((run) => run.run_id === selectedRunId) ?? null;
  const cases = detail?.cases ?? [];
  const visibleCases = useMemo(() => {
    if (filter === "all") return cases;
    if (filter === "failed") return cases.filter((item) => item.status !== "passed");
    if (filter === "passed" || filter === "error") return cases.filter((item) => item.status === filter);
    if (filter === "production_derived" || filter === "boundary") return cases.filter((item) => item.origin === filter);
    if (filter === "deterministic") return cases.filter((item) => item.deterministic_checks.some((check) => !check.passed));
    return cases.filter((item) => item.judge.status === "error" || item.judge.passed === false);
  }, [cases, filter]);
  const uniqueCases = useMemo(() => {
    const unique = new Map(cases.map((item) => [item.case_id, item]));
    return [...unique.values()];
  }, [cases]);
  const productionCount = uniqueCases.filter((item) => item.origin === "production_derived").length;
  const boundaryCount = uniqueCases.filter((item) => item.origin === "boundary").length;
  const judgeObservational = summary?.judge_policy.enforced === false;

  if (!initialRuns.length) {
    return (
      <div className="page-stack">
        <header className="page-header">
          <p className="section-label">隔离工具验收</p>
          <h1 className="page-heading">Agent 任务质量</h1>
          <p className="page-subtitle">检查 Agent 是否选对工具、填对参数，并根据固定工具证据完成当前任务。</p>
        </header>
        <div className="empty-state py-20">
          <FileCheck2 className="h-9 w-9 text-slate-400" />
          <p className="text-base font-bold text-slate-800">还没有 Agent 评测报告</p>
          <p className="max-w-lg text-xs leading-5 text-slate-500">
            运行独立评测容器后，报告会出现在这里。页面只读取结果，不会直接启动付费评测。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="page-stack">
      <header className="page-header">
        <div className="page-header-row">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="section-label">隔离工具验收</p>
              {summary && (
                <span className={`status-pill ${summary.status === "completed" ? "pill-ok" : summary.status === "running" ? "pill-info" : "pill-warn"}`}>
                  {summary.status === "running" && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
                  {STATUS_LABELS[summary.status]}
                </span>
              )}
              {summary && <span className={`status-pill ${summary.run_mode === "real" ? "pill-info" : "pill-warn"}`}>{summary.run_mode === "real" ? "真实模型" : "Mock 验收"}</span>}
            </div>
            <h1 className="page-heading">Agent 任务质量</h1>
            <p className="page-subtitle">
              真实模型只能看到生产同款工具定义，但执行层返回固定证据；写操作只记录意图，不接触业务数据或企微。
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
            <span>题库 {summary.dataset_id} v{summary.dataset_version}</span>
            <span>每题 {summary.repeat_count} 轮</span>
            <span>Agent {summary.model}</span>
            <span>判断官 {summary.judge_policy.model}</span>
            <span>框架 {frameworkLabel(summary)}</span>
            <span>代码 {summary.git_sha.slice(0, 10)}</span>
            <span>{summary.counts.completed}/{summary.counts.total} 次已完成</span>
          </div>
        )}
      </header>

      {summary?.run_mode === "mock" && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="flex items-start gap-2">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
            <p className="leading-6">
              <span className="font-bold">当前是 Mock 链路验收。</span>
              {summary.evaluation_framework === "deepeval"
                ? "回复和语义判断结果为模拟；DeepEval 工具指标与代码硬规则真实执行，只证明失败规则和展示链路有效。"
                : "工具调用、回复和判断官结果均为模拟，只证明失败规则和展示链路有效。"}
            </p>
          </div>
        </div>
      )}

      {summary && !summary.judge_policy.enforced && (
        <div className="rounded-xl border border-slate-300 bg-slate-100 px-4 py-3 text-sm text-slate-700">
          <div className="flex items-start gap-2">
            <Gauge className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" />
            <p className="leading-6"><span className="font-bold">判断官暂不参与硬门槛。</span>{summary.judge_policy.reason}</p>
          </div>
        </div>
      )}

      {loadError && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          <div className="flex items-center gap-2 font-bold"><AlertOctagon className="h-4 w-4" />报告读取失败</div>
          <p className="mt-1 text-xs leading-5">{loadError}</p>
        </div>
      )}

      {cases.length > 0 && summary && (
        <section className="grid overflow-hidden rounded-2xl border border-slate-200 bg-white sm:grid-cols-2 lg:grid-cols-4" aria-label="Agent 评测构成">
          {[
            ["固定题", uniqueCases.length, "同一题库重复运行便于比较"],
            ["真实高频", productionCount, "由线上高频任务脱敏抽象"],
            ["权限边界", boundaryCount, "覆盖写操作、澄清与禁用动作"],
            ["重复轮次", summary.repeat_count, `本批次共 ${summary.counts.total} 次执行`],
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
      )}

      {loading && !detail ? (
        <div className="panel flex min-h-64 items-center justify-center text-sm font-semibold text-slate-500">
          <LoaderCircle className="mr-2 h-5 w-5 animate-spin text-cyan-600" />正在读取评测明细…
        </div>
      ) : summary ? (
        <>
          <EvaluationCostPanel usage={summary.usage} />

          <section className="panel overflow-hidden">
            <div className="panel-header-clean">
              <div className="flex items-center gap-2">
                <Gauge className="h-5 w-5 text-cyan-700" />
                <h2 className="panel-title">任务正确性</h2>
              </div>
              <p className="panel-subtitle">代码硬错误直接判失败；未校准的 LLM 判断单独展示语义诊断。</p>
            </div>
            <div className="grid min-w-0 sm:grid-cols-2 lg:grid-cols-5 lg:divide-x lg:divide-slate-100">
              <MetricItem label="硬规则通过率" hint="回复方式、工具、参数、禁止动作和字面约束全部通过。" metric={summary.metrics.task_pass_rate} />
              <MetricItem label="工具选择正确率" hint="该调用的工具被调用，禁止和计划外工具未出现。" metric={summary.metrics.tool_selection_accuracy} />
              <MetricItem label="关键参数正确率" hint="日期、对象、动作等关键参数与题目一致。" metric={summary.metrics.argument_accuracy} />
              <MetricItem label="未授权操作率" hint="任何未获授权的写操作都计入；目标为 0。" metric={summary.metrics.unauthorized_action_rate} lowerIsBetter />
              <MetricItem label="字面约束通过率" hint="只统计需要精确出现的数字、日期、编号或授权提示。" metric={summary.metrics.literal_constraint_pass_rate} />
            </div>
            <div className="border-t border-slate-200">
              <div className="px-5 pt-4 sm:px-6">
                <div className="flex flex-wrap items-center gap-2">
                  <Gauge className="h-4 w-4 text-slate-500" />
                  <h3 className="text-sm font-extrabold text-slate-900">
                    {summary.evaluation_framework === "deepeval" ? "DeepEval 语义指标" : "LLM 判断官"}
                  </h3>
                  {!summary.judge_policy.enforced && <span className="status-pill pill-neutral">仅观察</span>}
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-500">{summary.judge_policy.reason}</p>
              </div>
              <div className="grid min-w-0 sm:grid-cols-2 lg:grid-cols-5 lg:divide-x lg:divide-slate-100">
                <MetricItem label="综合通过率" hint="任务完成、证据忠实和澄清合理三项同时通过。" metric={summary.metrics.judge_pass_rate} observational={!summary.judge_policy.enforced} />
                <MetricItem label="任务完成" hint="有没有真正完成用户当前要办的事。" metric={summary.metrics.judge_task_completion_rate} observational={!summary.judge_policy.enforced} />
                <MetricItem label="证据忠实" hint="结论是否都能由对话或工具返回支持。" metric={summary.metrics.judge_groundedness_rate} observational={!summary.judge_policy.enforced} />
                <MetricItem label="澄清合理" hint="信息不足时只问必要项，充分时不多问。" metric={summary.metrics.judge_clarification_rate} observational={!summary.judge_policy.enforced} />
                <MetricItem label="表达质量" hint="清晰、自然、克制；始终不作为硬门槛。" metric={summary.metrics.judge_expression_rate} observational />
              </div>
            </div>
          </section>

          <section className="panel overflow-hidden">
            <div className="panel-header-clean">
              <div className="toolbar-row">
                <div>
                  <div className="flex items-center gap-2">
                    <Bot className="h-5 w-5 text-cyan-700" />
                    <h2 className="panel-title">逐题证据</h2>
                  </div>
                  <p className="panel-subtitle">展开一题即可核对对话、工具调用、固定返回、最终回复和两类判定。</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {([
                    ["all", `全部 ${summary.counts.total}`],
                    ["failed", `未通过 ${summary.counts.failed + summary.counts.errors}`],
                    ["passed", `通过 ${summary.counts.passed}`],
                    ["error", `运行错误 ${summary.counts.errors}`],
                    ["production_derived", `真实高频 ${productionCount}`],
                    ["boundary", `边界题 ${boundaryCount}`],
                    ["deterministic", "代码检查失败"],
                    ["judge", "判断官异常"],
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
                  const key = `${item.case_id}-${item.round_index}`;
                  const expanded = expandedCase === key;
                  return (
                    <article key={key}>
                      <button
                        type="button"
                        onClick={() => setExpandedCase(expanded ? null : key)}
                        aria-expanded={expanded}
                        className="flex w-full min-w-0 items-center gap-3 px-4 py-4 text-left transition hover:bg-slate-50 sm:px-6"
                      >
                        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${item.status === "passed" ? "bg-emerald-100 text-emerald-700" : item.status === "error" ? "bg-amber-100 text-amber-700" : "bg-rose-100 text-rose-700"}`}>
                          {item.status === "passed" ? <CheckCircle2 className="h-4 w-4" /> : item.status === "error" ? <CircleDashed className="h-4 w-4" /> : <TriangleAlert className="h-4 w-4" />}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-bold leading-6 text-slate-950">{item.user_query}</span>
                            <CaseStatus status={item.status} />
                          </span>
                          <span className="mt-1 block text-xs leading-5 text-slate-500">{item.title}</span>
                          <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] font-semibold text-slate-500">
                            <span className={item.origin === "production_derived" ? "text-violet-700" : "text-cyan-700"}>{item.origin === "production_derived" ? "真实高频" : "边界题"}</span>
                            <span className="text-slate-300">·</span>
                            <span>{item.scenario}</span>
                            <span className="text-slate-300">·</span>
                            <span>第 {item.round_index} 轮</span>
                            <span className="text-slate-300">·</span>
                            <span className="flex items-center gap-1"><Wrench className="h-3 w-3" />{item.actual.tool_calls.length} 次工具</span>
                          </span>
                        </span>
                        <span className="hidden shrink-0 items-center gap-1 text-xs font-semibold text-slate-400 sm:flex">
                          <Clock3 className="h-3.5 w-3.5" />{item.elapsed_ms ?? 0}ms
                        </span>
                        <ChevronRight className={`h-5 w-5 shrink-0 text-slate-400 transition ${expanded ? "rotate-90" : ""}`} />
                      </button>
                      {expanded && <EvidenceDetail item={item} judgeObservational={judgeObservational} />}
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="empty-state m-5 sm:m-6">
                <MessageSquareText className="h-8 w-8 text-slate-400" />
                <p className="font-bold text-slate-800">当前筛选下没有评测用例</p>
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
