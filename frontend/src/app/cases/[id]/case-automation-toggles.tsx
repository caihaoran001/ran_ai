"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { Bot } from "lucide-react";

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

  async function toggle() {
    if (disabled) return;
    setBusy(true);
    const next = !on;
    const res = await fetch(`${API_BASE}/service-cases/${serviceCaseId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ [field]: next }),
    });
    if (res.ok) {
      setOn(next);
      onUpdated?.(next);
    }
    setBusy(false);
  }

  return (
    <div className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-start gap-3">
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-900">{title}</div>
          <p className="mt-0.5 text-xs leading-5 text-slate-500">{desc}</p>
        </div>
      </div>
      <label className="relative inline-flex shrink-0 cursor-pointer items-center self-start sm:self-center">
        <input type="checkbox" checked={on} disabled={busy || disabled} onChange={toggle} className="sr-only peer" />
        <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-cyan-700 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-cyan-300 peer-disabled:cursor-not-allowed peer-disabled:opacity-50" />
      </label>
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
