"use client";

import { useEffect, useState } from "react";
import { Database, Gauge, LoaderCircle } from "lucide-react";
import type { DiagnosticsSummary } from "@/lib/api";

// 数据看板右上角的系统状态条：数据库状态进页面自动查（纯 SELECT 1，零成本），
// AI 接口延迟按需测速——点击「测速」才真实调用一次大模型，避免每次进看板都产生费用。
export function SystemStatusWidget() {
  const [dbStatus, setDbStatus] = useState<{ status: string; message: string } | null>(null);
  const [dbError, setDbError] = useState(false);
  const [aiProbe, setAiProbe] = useState<DiagnosticsSummary["ai_probe"] | null>(null);
  const [probing, setProbing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/backend/diagnostics/summary?probe_ai=false", { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      })
      .then((data: DiagnosticsSummary) => {
        if (cancelled) return;
        setDbStatus(data.database);
        setAiProbe(data.ai_probe);
      })
      .catch(() => {
        if (!cancelled) setDbError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function probeAi() {
    if (probing) return;
    setProbing(true);
    try {
      const res = await fetch("/api/backend/diagnostics/summary?probe_ai=true", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      const data: DiagnosticsSummary = await res.json();
      setAiProbe(data.ai_probe);
    } catch {
      setAiProbe({ status: "failed", message: "测速请求失败", latency_ms: null, model: aiProbe?.model ?? null });
    } finally {
      setProbing(false);
    }
  }

  const dbOk = dbStatus?.status === "connected";
  const aiOk = aiProbe?.status === "connected";
  const aiFailed = aiProbe?.status === "failed" || aiProbe?.status === "not_configured";

  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* 数据库状态 */}
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${
          dbError || dbStatus?.status === "failed"
            ? "border-rose-200 bg-rose-50 text-rose-700"
            : dbOk
              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
              : "border-slate-200 bg-slate-50 text-slate-500"
        }`}
        title={dbError ? "状态查询失败" : dbStatus?.message ?? "检测中"}
      >
        <Database className="h-3.5 w-3.5" />
        数据库
        <span className="font-bold">
          {dbError || dbStatus?.status === "failed" ? "异常" : dbOk ? "正常" : "…"}
        </span>
      </span>

      {/* AI 接口延迟：点击测速才真实调用一次大模型 */}
      <button
        type="button"
        onClick={probeAi}
        disabled={probing}
        title={aiProbe?.message ?? "点击真实调用一次大模型测量延迟"}
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold transition-colors disabled:cursor-wait ${
          aiOk
            ? "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
            : aiFailed
              ? "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100"
              : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100"
        }`}
      >
        {probing ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Gauge className="h-3.5 w-3.5" />}
        AI接口
        <span className="font-bold">
          {probing
            ? "测速中…"
            : aiOk && typeof aiProbe?.latency_ms === "number"
              ? `${aiProbe.latency_ms}ms`
              : aiFailed
                ? "异常"
                : "点击测速"}
        </span>
      </button>
    </div>
  );
}
