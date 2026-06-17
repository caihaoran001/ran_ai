"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE } from "@/lib/api";

export function PendingMessageActions({
  messageId,
  initialContent,
}: {
  messageId: number;
  initialContent: string;
}) {
  const router = useRouter();
  const [content, setContent] = useState(initialContent);
  const [busy, setBusy] = useState(false);

  async function save() {
    await fetch(`${API_BASE}/messages/${messageId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content }),
    });
  }

  async function send() {
    setBusy(true);
    await save();
    await fetch(`${API_BASE}/messages/${messageId}/send`, { method: "POST" });
    setBusy(false);
    router.refresh();
  }

  async function discard() {
    setBusy(true);
    await fetch(`${API_BASE}/messages/${messageId}/discard`, { method: "POST" });
    setBusy(false);
    router.refresh();
  }

  return (
    <div className="space-y-3">
      <Textarea className="min-h-32 text-sm" value={content} onChange={(event) => setContent(event.target.value)} />
      <div className="flex flex-wrap gap-2">
        <Button type="button" className="bg-emerald-700 hover:bg-emerald-600" onClick={send} disabled={busy}>
          {busy ? "处理中..." : "确认发送"}
        </Button>
        <Button
          type="button"
          className="border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
          onClick={discard}
          disabled={busy}
        >
          不发送
        </Button>
      </div>
    </div>
  );
}
