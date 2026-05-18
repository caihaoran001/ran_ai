"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE } from "@/lib/api";

export function PendingMessageActions({
  messageId,
  serviceCaseId,
  initialContent,
}: {
  messageId: number;
  serviceCaseId?: number | null;
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

  async function markIntervention() {
    if (!serviceCaseId) return;
    setBusy(true);
    await fetch(`${API_BASE}/service-cases/${serviceCaseId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ needs_human_intervention: true, ai_auto_reply_enabled: false }),
    });
    setBusy(false);
    router.refresh();
  }

  return (
    <div className="space-y-3">
      <Textarea className="min-h-32 text-sm" value={content} onChange={(event) => setContent(event.target.value)} />
      <div className="flex flex-wrap gap-2">
        <Button type="button" className="bg-emerald-700 hover:bg-emerald-600" onClick={send} disabled={busy}>
          {busy ? "发送中..." : "确认发送"}
        </Button>
      </div>
    </div>
  );
}
