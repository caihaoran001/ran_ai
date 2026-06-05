"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { Send } from "lucide-react";

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
    <div className="panel p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-slate-950">文件自动发送策略</h3>
          <p className="mt-1 text-sm leading-6 text-slate-500">
            控制《处方单》及《阶段报告》的自动下发。开启后，非风险文件将跳过审核直接发送给用户。
          </p>
        </div>
        <label className="relative inline-flex cursor-pointer items-center">
          <input
            type="checkbox"
            checked={isAutoSend}
            disabled={busy}
            onChange={toggle}
            className="sr-only peer"
          />
          <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-cyan-700 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-cyan-300" />
        </label>
      </div>
      {isAutoSend && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-cyan-100 bg-cyan-50 p-3 text-sm text-cyan-800">
          <Send className="mt-0.5 h-4 w-4 shrink-0" />
          已开启自动交付模式。系统将自动发送低风险文件，提升运营效率。
        </div>
      )}
    </div>
  );
}
