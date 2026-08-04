"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { AlertCircle, Bot, LoaderCircle } from "lucide-react";

type ToggleKey = "ai_auto_reply_enabled";

function Row({
  serviceCaseId,
  field,
  initial,
  icon: Icon,
  title,
  desc,
  disabled = false,
  onUpdated,
}: {
  serviceCaseId: number;
  field: ToggleKey;
  initial: boolean;
  icon: typeof Bot;
  title: string;
  desc?: string;
  disabled?: boolean;
  onUpdated?: (value: boolean) => void;
}) {
  const [on, setOn] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (disabled || busy) return;
    setBusy(true);
    setError(null);
    const next = !on;
    try {
      const res = await fetch(`${API_BASE}/service-cases/${serviceCaseId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ [field]: next }),
      });
      if (!res.ok) {
        throw new Error((await res.text()) || "保存失败，请稍后再试。");
      }
      setOn(next);
      onUpdated?.(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
        <span className="text-sm font-medium text-slate-900">{title}</span>
        {desc && <p className="text-xs leading-5 text-slate-500">{desc}</p>}
        {busy && (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-cyan-700" role="status">
            <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            保存中…
          </span>
        )}
        <label className="relative inline-flex cursor-pointer items-center" aria-busy={busy || undefined}>
          <input type="checkbox" checked={on} disabled={busy || disabled} onChange={toggle} className="sr-only peer" />
          <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-cyan-700 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-cyan-300 peer-disabled:cursor-not-allowed peer-disabled:opacity-50" />
        </label>
      </div>
      {error && (
        <p className="mt-1.5 flex items-start gap-1.5 text-xs font-medium text-rose-700" role="alert">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          设置未改变：{error}
        </p>
      )}
    </div>
  );
}

export function CaseAutomationToggles({
  serviceCaseId,
  aiAutoReplyEnabled,
}: {
  serviceCaseId: number;
  aiAutoReplyEnabled: boolean;
}) {
  return (
    <div className="panel px-4 py-3">
      <div className="flex items-center justify-between gap-4">
        <h3 className="text-sm font-semibold text-slate-950">自动化设置（仅本服务单）</h3>
        <Row
          serviceCaseId={serviceCaseId}
          field="ai_auto_reply_enabled"
          initial={aiAutoReplyEnabled}
          icon={Bot}
          title="AI 自动回复"
        />
      </div>
    </div>
  );
}
