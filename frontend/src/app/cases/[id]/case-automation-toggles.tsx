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
  desc: string;
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
    <div className="py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
          <div className="min-w-0">
            <div className="text-sm font-medium text-slate-900">{title}</div>
            <p className="mt-0.5 text-xs leading-5 text-slate-500">{desc}</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2 self-start sm:self-center">
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
      </div>
      {error && (
        <p className="mt-2 flex items-start gap-1.5 text-xs font-medium text-rose-700" role="alert">
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
    <div className="panel p-5">
      <h3 className="font-semibold text-slate-950">自动化设置（仅本服务单）</h3>
      <p className="mt-1 text-xs leading-5 text-slate-500">
        以下设置仅对本服务单生效，不影响其他服务单。
      </p>

      <div className="mt-3 divide-y divide-slate-100">
        <div className="pb-1 pt-2 text-xs font-medium text-slate-400">群内机器人</div>
        <Row
          serviceCaseId={serviceCaseId}
          field="ai_auto_reply_enabled"
          initial={aiAutoReplyEnabled}
          icon={Bot}
          title="AI 自动回复"
          desc="关闭后机器人在群里完全不说话（等同群内「@机器人 退下」）；开启等同「回来」。"
        />
      </div>
    </div>
  );
}
