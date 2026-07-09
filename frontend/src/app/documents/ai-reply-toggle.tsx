"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { Bot, BotOff } from "lucide-react";

export function AiReplyToggle({ initialValue }: { initialValue: boolean }) {
  const [enabled, setEnabled] = useState(initialValue);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    const next = !enabled;
    await fetch(`${API_BASE}/settings/ai-reply-enabled`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled: next }),
    });
    setEnabled(next);
    setBusy(false);
  }

  return (
    <div className="panel p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-slate-950">AI 聊天回复总开关</h3>
          <p className="mt-1 text-sm leading-6 text-slate-500">
            控制机器人在群内是否自动回复。开启后日常咨询由 AI 直接回复到群内；关闭后 AI 完全不回复（仍保留高风险自动转人工）。
          </p>
        </div>
        <label className="relative inline-flex cursor-pointer items-center">
          <input
            type="checkbox"
            checked={enabled}
            disabled={busy}
            onChange={toggle}
            className="sr-only peer"
          />
          <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-cyan-700 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-cyan-300" />
        </label>
      </div>
      {enabled ? (
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
