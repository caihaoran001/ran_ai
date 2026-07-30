"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { AlertCircle, LoaderCircle, Send } from "lucide-react";

export function AutoSendToggle({ initialValue }: { initialValue: boolean }) {
  const [isAutoSend, setIsAutoSend] = useState(initialValue);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (busy) return;
    const next = !isAutoSend;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/settings/content-delivery-policy`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: next ? "auto_send" : "manual_review" }),
      });
      if (!response.ok) {
        throw new Error((await response.text()) || "保存失败，请稍后再试。");
      }
      setIsAutoSend(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-slate-950">文件自动发送策略</h3>
          <p className="mt-1 text-sm leading-6 text-slate-500">
            控制《入营交付文件》及《阶段报告》的自动下发。开启后，非风险文件将跳过审核直接发送给用户。
          </p>
        </div>
        <div className="ml-4 flex shrink-0 items-center gap-2">
          {busy && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-cyan-700" role="status">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              保存中…
            </span>
          )}
          <label className="relative inline-flex cursor-pointer items-center" aria-busy={busy || undefined}>
            <input
              type="checkbox"
              checked={isAutoSend}
              disabled={busy}
              onChange={toggle}
              className="sr-only peer"
            />
            <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-cyan-700 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-cyan-300 peer-disabled:cursor-wait peer-disabled:opacity-60" />
          </label>
        </div>
      </div>
      {error && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700" role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          自动发送策略未改变：{error}
        </div>
      )}
      {isAutoSend && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-cyan-100 bg-cyan-50 p-3 text-sm text-cyan-800">
          <Send className="mt-0.5 h-4 w-4 shrink-0" />
          已开启自动交付模式。系统将自动发送低风险文件，提升运营效率。
        </div>
      )}
    </div>
  );
}
