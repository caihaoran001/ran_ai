"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { API_BASE } from "@/lib/api";
import { LoaderCircle, Timer } from "lucide-react";

export function CaseActions({
  caseId,
  currentStartedAt,
}: {
  caseId: number;
  currentStartedAt?: string | null;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [isRefreshing, startTransition] = useTransition();
  const pending = busy || isRefreshing;

  // Calculate current days served
  const getCurrentDays = () => {
    if (!currentStartedAt) return 0;
    const started = new Date(currentStartedAt).getTime();
    const diffMs = Date.now() - started;
    if (!Number.isFinite(started) || diffMs < 0) return 0;
    return Math.floor(diffMs / 86400000);
  };

  const [days, setDays] = useState<string>(String(getCurrentDays()));
  const router = useRouter();

  async function saveDays() {
    setBusy(true);
    try {
      const daysNum = Math.max(0, parseInt(days) || 0);
      // We calculate started_at as exactly (daysNum) days ago in the past
      const targetDate = new Date(Date.now() - daysNum * 24 * 60 * 60 * 1000);
      const startedAtIso = targetDate.toISOString();

      const res = await fetch(`${API_BASE}/service-cases/${caseId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ started_at: startedAtIso }),
      });
      if (res.ok) {
        startTransition(() => {
          setEditing(false);
          router.refresh();
        });
      } else {
        alert("保存失败：" + (await res.text()));
      }
    } catch (err) {
      alert("保存失败");
      console.error(err);
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/50 p-2 transition-all">
        <span className="text-xs text-slate-500 font-medium">设定已服务：</span>
        <input
          type="number"
          min="0"
          max="28"
          value={days}
          onChange={(e) => setDays(e.target.value)}
          className="h-9 w-14 rounded border border-slate-300 bg-white text-center text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-emerald-100"
          disabled={pending}
        />
        <span className="text-xs text-slate-500 font-medium">天</span>
        <button
          onClick={saveDays}
          disabled={pending}
          aria-busy={pending || undefined}
          className="min-h-9 rounded bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 transition-colors hover:bg-emerald-100"
        >
          <span className="inline-flex items-center gap-1">
            {pending && <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />}
            {pending ? "保存中…" : "确认"}
          </span>
        </button>
        <button
          onClick={() => setEditing(false)}
          disabled={pending}
          className="min-h-9 rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 hover:text-slate-700"
        >
          取消
        </button>
      </div>
    );
  }

  return (
    <button
      onClick={() => {
        setDays(String(getCurrentDays()));
        setEditing(true);
      }}
      className="inline-flex min-h-10 items-center justify-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:border-cyan-300 hover:bg-cyan-50/40"
    >
      <Timer className="h-4 w-4 text-cyan-700" />
      调整服务天数
    </button>
  );
}
