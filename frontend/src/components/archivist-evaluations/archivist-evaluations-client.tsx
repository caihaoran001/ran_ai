"use client";

import { useEffect, useState } from "react";
import { ClipboardCheck, LoaderCircle } from "lucide-react";
import { api } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";

export type ArchivistEvaluationSummary = {
  schema_version: number;
  run_id: string;
  label?: string | null;
  started_at: string;
  mode: string;
  model: string;
  suite: string;
  groups: number;
  repeats: number;
  group_runs: number;
  passed: number;
  failed: number;
  flagged: number;
  batches: number;
  total_tokens: number;
  total_cost_yuan: number;
};

export type ArchivistEvaluationRunList = {
  items: ArchivistEvaluationSummary[];
};

type ArchivistMessage = {
  sender: string;
  content: string;
  time?: string | null;
};

type ArchivistCaseBatch = {
  label: string;
  messages: ArchivistMessage[];
};

type ArchivistEmit = {
  name: string;
  args: Record<string, unknown>;
};

type ArchivistUsage = {
  model?: string | null;
  total_tokens?: number | null;
  estimated_cost_yuan?: number | null;
} | null;

type ArchivistDBChanges = {
  checkins_added: Array<{ date: string; parsed: Record<string, unknown> }>;
  checkins_updated: Array<{ date: string; changes: Record<string, unknown> }>;
  outcomes_added: Array<{
    field_key: string;
    event_type: string;
    value_text?: string | null;
  }>;
  state_changed: boolean;
  memory_changed: boolean;
  drafts_added: Array<{
    draft_type?: string | null;
    title?: string | null;
    scope?: string | null;
  }>;
};

type ArchivistBatchResult = {
  batch_index: number;
  label: string;
  watermark_from?: number | null;
  watermark_to?: number | null;
  emitted: ArchivistEmit[];
  db_changes: ArchivistDBChanges;
  usage?: ArchivistUsage;
};

type ArchivistRepeat = {
  repeat_index: number;
  verdict: string;
  failed_assertions: string[];
  flags: string[];
  batch_results: ArchivistBatchResult[];
};

export type ArchivistCase = {
  group_code: string;
  title: string;
  expectation: string;
  batches: ArchivistCaseBatch[];
  repeats: ArchivistRepeat[];
};

export type ArchivistEvaluationRunDetail = {
  summary: ArchivistEvaluationSummary;
  cases: ArchivistCase[];
};

const SENDER_LABELS: Record<string, string> = {
  user: "用户",
  staff: "班主任",
  bot: "助教",
  system: "系统提醒",
};

function verdictDotClass(verdict: string): string {
  if (verdict === "PASS") return "bg-emerald-500";
  if (verdict === "FAIL") return "bg-rose-500";
  return "bg-amber-400";
}

function VerdictDots({ repeats }: { repeats: ArchivistRepeat[] }) {
  return (
    <span className="flex items-center gap-1">
      {repeats.map((item) => (
        <span
          key={item.repeat_index}
          className={`h-2.5 w-2.5 rounded-full ${verdictDotClass(item.verdict)}`}
          title={`第 ${item.repeat_index + 1} 次：${item.verdict}`}
        />
      ))}
    </span>
  );
}

function compact(value: unknown, limit = 240): string {
  let text: string;
  try {
    text = JSON.stringify(value);
  } catch {
    text = String(value);
  }
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function BatchEvidence({ batch }: { batch: ArchivistBatchResult }) {
  const db = batch.db_changes;
  const emptyDb =
    !db.checkins_added.length &&
    !db.checkins_updated.length &&
    !db.outcomes_added.length &&
    !db.state_changed &&
    !db.memory_changed &&
    !db.drafts_added.length;
  return (
    <div className="space-y-2 rounded-lg border border-slate-100 bg-white p-3">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
        <span className="font-bold text-slate-500">第 {batch.batch_index + 1} 批</span>
        {batch.label ? <span>{batch.label}</span> : null}
        {batch.watermark_to != null ? <span>水位 → {batch.watermark_to}</span> : null}
        {batch.usage?.total_tokens ? <span>{batch.usage.total_tokens} tokens</span> : null}
      </div>
      <div>
        <p className="text-[11px] font-black text-slate-600">模型上报</p>
        {batch.emitted.length ? (
          <ul className="mt-1 space-y-1">
            {batch.emitted.map((call, index) => (
              <li
                key={index}
                className="rounded bg-slate-50 px-2 py-1 text-[11px] leading-4 text-slate-600"
              >
                <span className="font-bold text-slate-700">{call.name}</span>
                <span className="ml-2 break-all text-slate-400">
                  {compact(call.args)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[11px] text-slate-400">没有调用任何上报工具</p>
        )}
      </div>
      <div>
        <p className="text-[11px] font-black text-slate-600">入库变化</p>
        <ul className="mt-1 space-y-1 text-[11px] leading-4 text-slate-600">
          {db.checkins_added.map((row) => (
            <li key={`ca-${row.date}`}>
              新增打卡 <b>{row.date}</b>：{compact(row.parsed, 200)}
            </li>
          ))}
          {db.checkins_updated.map((row) => (
            <li key={`cu-${row.date}`}>
              更新打卡 <b>{row.date}</b>：{compact(row.changes, 200)}
            </li>
          ))}
          {db.outcomes_added.map((row, index) => (
            <li key={`oa-${index}`}>
              新增成效 <b>{row.field_key}</b> · {row.event_type}
              {row.value_text ? `：${row.value_text}` : ""}
            </li>
          ))}
          {db.state_changed ? <li>状态画像有变化</li> : null}
          {db.memory_changed ? <li>用户小结被改写</li> : null}
          {db.drafts_added.map((row, index) => (
            <li key={`da-${index}`}>
              新建草稿：{row.title || row.draft_type || "未命名"}
              {row.scope ? `（${row.scope}）` : ""}
            </li>
          ))}
          {emptyDb ? <li className="text-slate-400">无入库变化</li> : null}
        </ul>
      </div>
    </div>
  );
}

function CaseCard({ item }: { item: ArchivistCase }) {
  const passCount = item.repeats.filter((repeat) => repeat.verdict === "PASS").length;
  return (
    <details className="panel overflow-hidden">
      <summary className="flex cursor-pointer flex-wrap items-center gap-3 px-4 py-3">
        <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[11px] font-black text-white">
          {item.group_code}
        </span>
        <span className="text-sm font-black text-slate-800">{item.title}</span>
        <span className="min-w-0 flex-1 truncate text-[11px] text-slate-400">
          {item.expectation}
        </span>
        <VerdictDots repeats={item.repeats} />
        <span
          className={`text-xs font-bold ${
            passCount === item.repeats.length ? "text-emerald-600" : "text-rose-600"
          }`}
        >
          {passCount}/{item.repeats.length} 过
        </span>
      </summary>
      <div className="space-y-3 border-t border-slate-100 bg-slate-50/40 px-4 py-3">
        <div>
          <p className="text-[11px] font-black text-slate-600">题目（按批）</p>
          <div className="mt-1 space-y-2">
            {item.batches.map((batch) => (
              <div
                key={batch.label}
                className="rounded-lg border border-slate-100 bg-white p-2.5"
              >
                <p className="text-[11px] font-bold text-slate-500">{batch.label}</p>
                <ul className="mt-1 space-y-1">
                  {batch.messages.map((message, index) => (
                    <li key={index} className="text-xs leading-5 text-slate-700">
                      <span className="mr-1 font-bold text-slate-500">
                        [{SENDER_LABELS[message.sender] ?? message.sender}]
                      </span>
                      {message.content}
                      {message.time ? (
                        <span className="ml-2 text-[11px] text-slate-400">
                          {message.time}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
        {item.repeats.map((repeat) => (
          <div
            key={repeat.repeat_index}
            className="rounded-lg border border-slate-100 bg-white p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`h-2.5 w-2.5 rounded-full ${verdictDotClass(repeat.verdict)}`}
              />
              <span className="text-xs font-black text-slate-700">
                第 {repeat.repeat_index + 1} 次 · {repeat.verdict}
              </span>
            </div>
            {repeat.failed_assertions.length ? (
              <ul className="mt-1.5 space-y-1 text-[11px] leading-4 text-rose-700">
                {repeat.failed_assertions.map((text, index) => (
                  <li key={index}>· {text}</li>
                ))}
              </ul>
            ) : null}
            {repeat.flags.length ? (
              <ul className="mt-1.5 space-y-1 text-[11px] leading-4 text-amber-700">
                {repeat.flags.map((text, index) => (
                  <li key={index}>· {text}</li>
                ))}
              </ul>
            ) : null}
            <div className="mt-2 space-y-2">
              {repeat.batch_results.map((batch) => (
                <BatchEvidence key={batch.batch_index} batch={batch} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}

export function ArchivistEvaluationsClient({
  initialRuns,
}: {
  initialRuns: ArchivistEvaluationSummary[];
}) {
  const [selectedRunId, setSelectedRunId] = useState(initialRuns[0]?.run_id ?? "");
  const [detail, setDetail] = useState<ArchivistEvaluationRunDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!selectedRunId) return;
    let active = true;
    setLoading(true);
    api<ArchivistEvaluationRunDetail>(`/archivist-evaluations/${selectedRunId}`)
      .then((value) => {
        if (active) setDetail(value);
      })
      .catch(() => {
        if (active) setDetail(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedRunId]);

  if (!initialRuns.length) {
    return (
      <div className="empty-state py-20">
        <ClipboardCheck className="h-8 w-8 text-slate-300" />
        <p className="mt-3 text-base font-bold text-slate-800">还没有档案官评测批次</p>
        <p className="mt-1 max-w-md text-xs leading-5 text-slate-500">
          运行档案官去重评测后，13 组题的通过情况会在这里按批次展示。
        </p>
      </div>
    );
  }

  const summary =
    detail?.summary ??
    initialRuns.find((run) => run.run_id === selectedRunId) ??
    null;
  if (!summary) return null;
  const allPass = summary.failed === 0 && summary.flagged === 0;

  return (
    <div className="space-y-4">
      <section className="panel overflow-hidden">
        <div className="border-b border-slate-200 bg-[linear-gradient(120deg,#f8fbff_0%,#ffffff_48%,#f6fbf8_100%)] px-4 py-4 sm:px-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-base font-black text-slate-900">
                  档案官去重评测 · 13 组题
                </p>
                <span
                  className={`rounded-full px-2 py-1 text-[11px] font-bold ${
                    summary.mode === "real"
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-slate-100 text-slate-500"
                  }`}
                >
                  {summary.mode === "real" ? "真实模型" : "假人（零成本）"}
                </span>
                <span
                  className={`rounded-full px-2 py-1 text-[11px] font-bold ${
                    allPass ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"
                  }`}
                >
                  {summary.passed}/{summary.group_runs} 组次通过
                </span>
              </div>
              <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">
                每组连续跑 {summary.repeats} 次：检查旧消息不重复入库、新信息不漏记、日期与证据归属正确。
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
                    {run.label ? `${run.label} · ` : ""}
                    {formatChinaShortDateTime(run.started_at)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="mt-4 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
              <p className="text-slate-400">通过</p>
              <p className="mt-0.5 text-base font-black text-slate-800">
                {summary.passed} / {summary.group_runs} 组次
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
              <p className="text-slate-400">批次规模</p>
              <p className="mt-0.5 text-base font-black text-slate-800">
                {summary.groups} 组 × {summary.repeats} 次 · {summary.batches} 批
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
              <p className="text-slate-400">模型</p>
              <p className="mt-0.5 text-base font-black text-slate-800">
                {summary.model}
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
              <p className="text-slate-400">费用</p>
              <p className="mt-0.5 text-base font-black text-slate-800">
                ¥{summary.total_cost_yuan.toFixed(2)}{" "}
                <span className="text-[11px] font-semibold text-slate-400">
                  {summary.total_tokens.toLocaleString()} tokens
                </span>
              </p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 px-4 py-2 text-[11px] text-slate-400 sm:px-5">
          {loading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}
          <span>
            {loading
              ? "读取批次详情…"
              : "点开任一组查看题目、失败原因与每批入库证据"}
          </span>
        </div>
      </section>

      {detail ? (
        <div className="space-y-3">
          {detail.cases.map((item) => (
            <CaseCard key={item.group_code} item={item} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
