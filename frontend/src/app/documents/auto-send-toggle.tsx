"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";

export function AutoSendToggle({ initialValue }: { initialValue: boolean }) {
  const [isAutoSend, setIsAutoSend] = useState(initialValue);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    await fetch(`${API_BASE}/settings/content-delivery-policy`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode: !isAutoSend ? "auto_send" : "manual_review" }),
    });
    setIsAutoSend(!isAutoSend);
    setBusy(false);
  }

  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-zinc-900">文件自动发送策略</h3>
          <p className="mt-1 text-sm text-zinc-500">
            控制《处方单》及《阶段报告》的自动下发。开启后，非风险文件将跳过审核直接发送给用户。
          </p>
        </div>
        <label className="relative inline-flex cursor-pointer items-center">
          <input
            type="checkbox"
            checked={isAutoSend}
            disabled={busy}
            onChange={toggle}
            className="peer absolute inset-0 z-10 cursor-pointer opacity-0"
          />
          <div className="peer h-6 w-11 rounded-full bg-zinc-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-blue-600 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300" />
        </label>
      </div>
      {isAutoSend && (
        <div className="mt-3 rounded-lg bg-blue-50 p-3 text-sm text-blue-700">
          🚀 已开启自动交付模式。系统将自动发送低风险文件，显著提升运营效率。
        </div>
      )}
    </div>
  );
}
