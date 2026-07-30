"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { AlertCircle, Bot, BotOff, LoaderCircle, ShieldAlert } from "lucide-react";

type Props = {
  initialValue: boolean;
  autoDisabledReason?: string | null;
  autoDisabledAt?: string | null;
};

function formatBeijing(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour12: false,
    });
  } catch {
    return "";
  }
}

export function AiReplyToggle({
  initialValue,
  autoDisabledReason = null,
  autoDisabledAt = null,
}: Props) {
  const [enabled, setEnabled] = useState(initialValue);
  const [reason, setReason] = useState<string | null>(autoDisabledReason);
  const [at, setAt] = useState<string | null>(autoDisabledAt);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (busy) return;
    setBusy(true);
    setError(null);
    const next = !enabled;
    try {
      const res = await fetch(`${API_BASE}/settings/ai-reply-enabled`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!res.ok) {
        throw new Error((await res.text()) || "保存失败，请稍后再试。");
      }
      const data = await res
        .json()
        .catch(() => ({ enabled: next, auto_disabled_reason: null, auto_disabled_at: null }));
      setEnabled(Boolean(data.enabled));
      setReason(data.auto_disabled_reason ?? null);
      setAt(data.auto_disabled_at ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  // 系统因企微风控自动关闭：enabled=false 且带有风控原因
  const riskDisabled = !enabled && !!reason;

  return (
    <div className="panel p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-slate-950">AI 聊天回复总开关</h3>
          <p className="mt-1 text-sm leading-6 text-slate-500">
            控制机器人在群内是否自动回复。开启后日常咨询由 AI 直接回复到群内；关闭后 AI 完全不回复（仍保留高风险自动转人工）。
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
              checked={enabled}
              disabled={busy}
              onChange={toggle}
              className="sr-only peer"
            />
            <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-cyan-700 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-cyan-300 peer-disabled:cursor-wait peer-disabled:opacity-60" />
          </label>
        </div>
      </div>
      {error ? (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700" role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          AI 回复开关未改变：{error}
        </div>
      ) : riskDisabled ? (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="space-y-1">
            <div className="font-semibold">企微账号被风控，AI 回复已自动关闭，请稍后再试。</div>
            <div className="text-rose-600/90">
              {reason}
              {at ? ` · ${formatBeijing(at)}` : ""}
            </div>
            <div className="text-rose-600/80">
              账号恢复后可手动重新开启；若仍被风控，系统会在下次触发时再次自动关闭。
            </div>
          </div>
        </div>
      ) : enabled ? (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-cyan-100 bg-cyan-50 p-3 text-sm text-cyan-800">
          <Bot className="mt-0.5 h-4 w-4 shrink-0" />
          AI 回复已开启。日常咨询由 AI 自动回复到群内，高风险内容仍自动转人工。
        </div>
      ) : (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
          <BotOff className="mt-0.5 h-4 w-4 shrink-0" />
          AI 回复已关闭。群内消息不会触发 AI 回复（高风险自动转人工不受影响）。
        </div>
      )}
    </div>
  );
}
