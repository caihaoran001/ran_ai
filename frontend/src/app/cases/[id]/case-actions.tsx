"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE } from "@/lib/api";

export function CaseActions({
  caseId,
  currentStartedAt,
}: {
  caseId: number;
  currentStartedAt?: string | null;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

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
        setEditing(false);
        router.refresh();
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
      <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-50/50 p-1 px-2.5 shadow-inner transition-all">
        <span className="text-xs text-zinc-500 font-medium">设定已服务：</span>
        <input
          type="number"
          min="0"
          max="28"
          value={days}
          onChange={(e) => setDays(e.target.value)}
          className="w-12 h-7 rounded border border-zinc-300 bg-white text-center text-sm font-semibold focus:outline-none focus:ring-1 focus:ring-emerald-500"
          disabled={busy}
        />
        <span className="text-xs text-zinc-500 font-medium">天</span>
        <button
          onClick={saveDays}
          disabled={busy}
          className="text-xs font-semibold text-emerald-600 hover:text-emerald-700 bg-emerald-50 hover:bg-emerald-100 rounded px-2.5 py-1 transition-all"
        >
          {busy ? "保存中" : "确认"}
        </button>
        <button
          onClick={() => setEditing(false)}
          disabled={busy}
          className="text-xs text-zinc-500 hover:text-zinc-700 rounded px-1 py-1"
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
      className="inline-flex h-9 items-center justify-center rounded-md border border-zinc-300 bg-white px-3 text-sm hover:bg-zinc-50 font-medium text-zinc-700 gap-1.5 shadow-sm transition-all hover:border-zinc-400"
    >
      ⏱️ 调整服务天数
    </button>
  );
}
