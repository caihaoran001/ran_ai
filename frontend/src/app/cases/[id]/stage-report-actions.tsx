"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE, UserDocument } from "@/lib/api";

const STAGES = [
  ["day3", "3天"],
  ["day7", "7天"],
  ["day14", "14天"],
  ["day21", "21天"],
  ["day28", "28天"],
  ["graduation", "毕业"],
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
  const generatedStages = new Set(
    documents
      .map((document) => document.content_json?.stage)
      .filter((stage): stage is string => Boolean(stage)),
  );

  async function generate(stage: string) {
    setBusyStage(stage);
    await fetch(`${API_BASE}/service-cases/${serviceCaseId}/stage-reports/generate?stage=${stage}`, {
      method: "POST",
    });
    setBusyStage(null);
    router.refresh();
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
            className={
              alreadyGenerated
                ? "cursor-not-allowed rounded-md border border-zinc-200 bg-zinc-100 px-3 py-2 text-sm text-zinc-400"
                : "rounded-md border border-zinc-200 px-3 py-2 text-sm text-zinc-700 hover:border-blue-300 hover:text-blue-700 disabled:opacity-50"
            }
          >
            {busyStage === stage ? "生成中..." : alreadyGenerated ? `${label}报告已生成` : `生成${label}报告`}
          </button>
        );
      })}
    </div>
  );
}
