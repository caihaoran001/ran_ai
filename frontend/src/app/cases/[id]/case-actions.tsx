"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { API_BASE } from "@/lib/api";

export function CaseActions({ caseId }: { caseId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);

  async function post(path: string, label: string) {
    setBusy(label);
    await fetch(`${API_BASE}${path}`, { method: "POST" });
    setBusy(null);
    router.refresh();
  }

  return (
    <div className="flex flex-wrap gap-2">
      <Button type="button" disabled={!!busy} onClick={() => post(`/service-cases/${caseId}/generate-profile`, "profile")}>
        {busy === "profile" ? "生成中" : "生成用户画像"}
      </Button>
      <Button type="button" disabled={!!busy} onClick={() => post(`/service-cases/${caseId}/generate-documents`, "documents")}>
        {busy === "documents" ? "生成中" : "生成交付文件（手动审核）"}
      </Button>
      <Button type="button" disabled={!!busy} className="bg-emerald-700 hover:bg-emerald-600" onClick={() => post(`/service-cases/${caseId}/generate-documents?auto_send=true`, "auto-documents")}>
        {busy === "auto-documents" ? "生成并发送中" : "生成后自动发送"}
      </Button>
      <Button type="button" disabled={!!busy} className="bg-white text-zinc-900 ring-1 ring-zinc-300 hover:bg-zinc-50" onClick={() => post(`/service-cases/${caseId}/documents/send`, "send-documents")}>
        {busy === "send-documents" ? "发送中" : "确认发送两份文件"}
      </Button>
    </div>
  );
}
