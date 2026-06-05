"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { Bot } from "lucide-react";

export function AiReplyToggle({ initialValue }: { initialValue: boolean }) {
  const [isAutoSend, setIsAutoSend] = useState(initialValue);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    const next = !isAutoSend;
    await fetch(`${API_BASE}/settings/ai-reply-delivery-policy`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode: next ? "auto_send" : "manual_review" }),
    });
    setIsAutoSend(next);
    setBusy(false);
  }

  return (
    <div className="panel p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-slate-950">AI 聊天回复策略</h3>
          <p className="mt-1 text-sm leading-6 text-slate-500">
            控制机器人在群内的实时响应。开启后，日常闲聊由 AI 自动回复，敏感或高风险意图将拦截至下方待审。
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
          <Bot className="mt-0.5 h-4 w-4 shrink-0" />
          已开启自动回复模式。AI 将处理日常咨询，高风险内容将自动触发人工拦截。
        </div>
      )}
    </div>
  );
}
