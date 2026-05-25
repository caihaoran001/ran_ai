"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { API_BASE } from "@/lib/api";

export function RetryGenerateDocuments({
  caseId,
  autoSend,
}: {
  caseId: number;
  autoSend: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);

  async function retryGenerate() {
    setBusy(true);
    setError(null);
    setQueued(false);
    try {
      const response = await fetch(
        `${API_BASE}/service-cases/${caseId}/generate-documents/retry?auto_send=${autoSend ? "true" : "false"}`,
        {method: "POST"},
      );
      if (!response.ok) {
        throw new Error(await response.text());
      }
      setQueued(true);
      setTimeout(() => router.refresh(), 1200);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "重试生成失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 flex flex-wrap items-start gap-3">
      <button
        type="button"
        onClick={retryGenerate}
        disabled={busy}
        className="inline-flex h-9 items-center justify-center rounded-md border border-red-200 bg-white px-3 text-sm font-medium text-red-700 shadow-sm transition-all hover:border-red-300 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {busy ? "重新生成中..." : "重试生成"}
      </button>
      {queued && !error && <p className="pt-2 text-sm text-red-700">已提交后台重新生成，你现在离开页面也不会中断。</p>}
      {error && <p className="pt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}
