"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle } from "lucide-react";
import { API_BASE, UserDocument } from "@/lib/api";

const STAGES = [
  ["day3", "3天"],
  ["day7", "7天"],
  ["day14", "14天"],
  ["day21", "21天"],
  ["day28", "28天"],
] as const;

export function StageReportActions({
  serviceCaseId,
  documents,
}: {
  serviceCaseId: number;
  documents: UserDocument[];
}) {
  const router = useRouter();
  const [busyStage, setBusyStage] = useState<string | null>(null);
  const [isRefreshing, startTransition] = useTransition();
  const [refreshStarted, setRefreshStarted] = useState(false);
  const generatedStages = new Set(
    documents
      .map((document) => document.content_json?.stage)
      .filter((stage): stage is string => Boolean(stage)),
  );

  useEffect(() => {
    if (refreshStarted && !isRefreshing) {
      setBusyStage(null);
      setRefreshStarted(false);
    }
  }, [isRefreshing, refreshStarted]);

  async function generate(stage: string) {
    setBusyStage(stage);
    let shouldRefresh = false;
    try {
      const res = await fetch(`${API_BASE}/service-cases/${serviceCaseId}/stage-reports/generate?stage=${stage}`, {
        method: "POST",
      });
      if (res.ok) {
        shouldRefresh = true;
        alert(`${STAGES.find(([s]) => s === stage)?.[1] || ""}报告生成成功！`);
      } else {
        const text = await res.text();
        alert(`生成失败: ${text || "服务器异常"}`);
      }
    } catch (e: any) {
      alert(`生成出错: ${e.message || e}`);
    } finally {
      if (shouldRefresh) {
        setRefreshStarted(true);
        startTransition(() => router.refresh());
      } else {
        setBusyStage(null);
      }
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      {STAGES.map(([stage, label]) => {
        const alreadyGenerated = generatedStages.has(stage);
        return (
          <button
            key={stage}
            type="button"
            disabled={busyStage !== null || alreadyGenerated}
            onClick={() => generate(stage)}
            aria-busy={busyStage === stage || undefined}
            className={
              alreadyGenerated
                ? "cursor-not-allowed rounded-md border border-slate-200 bg-slate-100 px-3 py-2 text-sm text-slate-400"
                : "rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-700 hover:border-blue-300 hover:text-blue-700 disabled:opacity-50"
            }
          >
            <span className="inline-flex items-center gap-1.5">
              {busyStage === stage && (
                <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              )}
              {busyStage === stage ? "生成中…" : alreadyGenerated ? `${label}报告已生成` : `生成${label}报告`}
            </span>
          </button>
        );
      })}
    </div>
  );
}
