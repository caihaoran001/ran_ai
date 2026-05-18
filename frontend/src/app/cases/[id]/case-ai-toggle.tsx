"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";

export function CaseAiToggle({
  serviceCaseId,
  initialEnabled,
}: {
  serviceCaseId: number;
  initialEnabled: boolean;
}) {
  const [enabled, setEnabled] = useState(initialEnabled);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    const next = !enabled;
    const response = await fetch(`${API_BASE}/service-cases/${serviceCaseId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ai_auto_reply_enabled: next }),
    });
    if (response.ok) setEnabled(next);
    setBusy(false);
  }

  return (
    <button
      type="button"
      disabled={busy}
      onClick={toggle}
      className={`rounded-md px-3 py-2 text-sm font-medium transition disabled:opacity-50 ${
        enabled
          ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 hover:bg-emerald-100"
          : "bg-zinc-900 text-white hover:bg-zinc-800"
      }`}
    >
      {enabled ? "AI 自动回复开启" : "AI 自动回复关闭"}
    </button>
  );
}
