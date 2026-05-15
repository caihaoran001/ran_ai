"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";

export function AutoSendToggle({ initialValue }: { initialValue: boolean }) {
  const [isAutoSend, setIsAutoSend] = useState(initialValue);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    await fetch(`${API_BASE}/settings/auto-send`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ auto_send_documents: !isAutoSend }),
    });
    setIsAutoSend(!isAutoSend);
    setBusy(false);
  }

  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-zinc-900">自动发送设置</h3>
          <p className="mt-1 text-sm text-zinc-500">
            开启后，所有服务单的文档生成后会自动发送到企业微信群
          </p>
        </div>
        <label className="relative inline-flex cursor-pointer items-center">
          <input
            type="checkbox"
            checked={isAutoSend}
            disabled={busy}
            onChange={toggle}
            className="peer sr-only"
          />
          <div className="peer h-6 w-11 rounded-full bg-zinc-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-blue-600 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300" />
        </label>
      </div>
      {isAutoSend && (
        <div className="mt-3 rounded-lg bg-blue-50 p-3 text-sm text-blue-700">
          已开启自动发送模式。文档生成后将直接发送到群，无需人工审核。
        </div>
      )}
    </div>
  );
}
