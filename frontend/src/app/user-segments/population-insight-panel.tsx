"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowRight,
  Lightbulb,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

import { api } from "@/lib/api";
import type { PopulationInsight } from "@/lib/api";


const REFRESH_REASON_LABELS: Record<string, string> = {
  initial: "首次生成",
  manual: "人工更新",
  weekly: "每周更新",
  new_users: "新增用户触发",
  prompt_updated: "洞察规则更新",
};

function formatDateTime(value: string | null) {
  if (!value) return "尚未生成";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function coverage(insight: PopulationInsight) {
  return insight.included_users > 0
    ? Math.round((insight.profiled_users * 100) / insight.included_users)
    : 0;
}

export function PopulationInsightPanel({
  initialInsight,
  loadFailed = false,
}: {
  initialInsight: PopulationInsight | null;
  loadFailed?: boolean;
}) {
  const router = useRouter();
  const [insight, setInsight] = useState(initialInsight);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [isRefreshingPage, startTransition] = useTransition();

  useEffect(() => {
    setInsight(initialInsight);
  }, [initialInsight]);

  useEffect(() => {
    if (insight?.status !== "generating") return;
    const timer = window.setTimeout(() => {
      startTransition(() => router.refresh());
    }, 5000);
    return () => window.clearTimeout(timer);
  }, [insight?.status, router]);

  async function refreshInsight() {
    setRefreshing(true);
    setError(null);
    try {
      const updated = await api<PopulationInsight>("/dashboard/population-insight/refresh", {
        method: "POST",
        body: "{}",
      });
      setInsight(updated);
      startTransition(() => router.refresh());
    } catch (err: unknown) {
      setError(err instanceof Error ? "本次更新失败，已保留上一版洞察。" : "共同洞察更新失败。");
      startTransition(() => router.refresh());
    } finally {
      setRefreshing(false);
    }
  }

  const busy = refreshing || isRefreshingPage || insight?.status === "generating";
  const content = insight?.content;
  const hasFailedRefresh = Boolean(error || insight?.last_refresh_failed);

  return (
    <section className="panel overflow-hidden" aria-labelledby="population-insight-title">
      <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-cyan-50 text-cyan-800">
            <Lightbulb className="h-4 w-4" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="population-insight-title" className="panel-title">群体共同洞察</h2>
              {insight && insight.status !== "empty" && (
                <span className="status-pill pill-info">{insight.evidence_level}</span>
              )}
            </div>
            <p className="panel-subtitle">从匿名个人画像中提炼共同任务，每周自动更新，页面查看不调用模型。</p>
          </div>
        </div>
        <button
          type="button"
          onClick={refreshInsight}
          disabled={busy || loadFailed}
          className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-3.5 text-sm font-bold text-slate-700 transition-colors duration-150 hover:border-cyan-300 hover:text-cyan-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} aria-hidden="true" />
          {busy ? "正在更新" : content ? "立即更新" : "生成洞察"}
        </button>
      </div>

      {(loadFailed || hasFailedRefresh) && (
        <div className="flex items-start gap-2 border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm leading-6 text-amber-950 sm:px-6" role="status">
          <AlertTriangle className="mt-1 h-4 w-4 shrink-0 text-amber-700" aria-hidden="true" />
          <p>
            {loadFailed
              ? "共同洞察暂时无法读取，其他群体画像统计不受影响。"
              : error ?? "上次更新失败，下方继续显示最近一版成功结果。"}
          </p>
        </div>
      )}

      {!content ? (
        <div className="flex flex-col gap-2 px-5 py-8 sm:px-6">
          <p className="font-bold text-slate-900">
            {insight?.status === "generating" ? "正在生成第一版共同洞察" : "尚未生成共同洞察"}
          </p>
          <p className="max-w-[72ch] text-sm leading-6 text-slate-600">
            {insight?.status === "generating"
              ? "模型只读取精简后的匿名画像，完成后会保存为快照。"
              : "点击“生成洞察”后，系统会对当前人群做一次结构化综合；之后每周最多自动更新一次。"}
          </p>
        </div>
      ) : (
        <>
          <div className="px-5 py-5 sm:px-6">
            <p className="max-w-[72ch] text-lg font-bold leading-7 tracking-tight text-slate-950">
              {content.headline}
            </p>
            <p className="mt-2 max-w-[75ch] text-sm leading-6 text-slate-700">{content.summary}</p>
          </div>

          <div className="border-y border-slate-200 bg-slate-50/70 px-5 py-4 sm:px-6">
            <p className="text-sm font-bold text-slate-900">共同 JTBD</p>
            <div className="mt-3 flex flex-col gap-3 text-sm leading-6 text-slate-700 md:flex-row md:items-stretch md:gap-0">
              {[
                ["当", content.common_jtbd.when],
                ["我希望", content.common_jtbd.want_to],
                ["从而", content.common_jtbd.so_i_can],
              ].map(([label, value], index) => (
                <div key={label} className="flex min-w-0 flex-1 items-start gap-2 md:items-center">
                  {index > 0 && <ArrowRight className="mt-1 hidden h-4 w-4 shrink-0 text-slate-400 md:block" aria-hidden="true" />}
                  <div className="min-w-0 md:px-3">
                    <span className="block text-xs font-bold text-slate-500">{label}</span>
                    <span className="mt-0.5 block text-slate-800">{value}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {content.segments.length > 0 && (
            <div className="px-5 py-5 sm:px-6">
              <h3 className="text-sm font-bold text-slate-900">主要人群分型</h3>
              <div className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
                {content.segments.map((segment) => (
                  <div key={`${segment.name}-${segment.entry_point}`} className="grid gap-2 py-3.5 md:grid-cols-[10rem_1fr_1fr] md:gap-5">
                    <p className="font-bold text-slate-900">{segment.name}</p>
                    <div>
                      <p className="text-xs font-bold text-slate-500">进入起点</p>
                      <p className="mt-0.5 text-sm leading-6 text-slate-700">{segment.entry_point}</p>
                    </div>
                    <div>
                      <p className="text-xs font-bold text-slate-500">深层目标</p>
                      <p className="mt-0.5 text-sm leading-6 text-slate-700">{segment.deeper_goal}</p>
                      <p className="mt-1 text-xs leading-5 text-slate-500">依据：{segment.evidence_basis}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="grid border-t border-slate-200 lg:grid-cols-2 lg:divide-x lg:divide-slate-200">
            <div className="px-5 py-4 sm:px-6">
              <h3 className="text-sm font-bold text-slate-900">共性与产品启示</h3>
              <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-700">
                {[...content.shared_patterns, ...content.product_implications].map((item, index) => (
                  <li key={`${index}-${item}`} className="flex gap-2">
                    <span className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan-700" aria-hidden="true" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="border-t border-slate-200 px-5 py-4 lg:border-t-0 sm:px-6">
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-slate-600" aria-hidden="true" />
                <h3 className="text-sm font-bold text-slate-900">证据边界</h3>
              </div>
              <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-600">
                {content.evidence_limits.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          </div>
        </>
      )}

      {insight && insight.generated_at && (
        <div className="flex flex-col gap-1 border-t border-slate-200 bg-slate-50/60 px-5 py-3 text-xs leading-5 text-slate-600 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-4 sm:px-6">
          <span>基于 {insight.included_users} 位用户，{insight.profiled_users} 位有入营画像（覆盖 {coverage(insight)}%）</span>
          <span>模型读取 {insight.model_input_users} 份匿名精简画像</span>
          <span>{formatDateTime(insight.generated_at)} 生成</span>
          {insight.refresh_reason && <span>{REFRESH_REASON_LABELS[insight.refresh_reason] ?? insight.refresh_reason}</span>}
        </div>
      )}
    </section>
  );
}
