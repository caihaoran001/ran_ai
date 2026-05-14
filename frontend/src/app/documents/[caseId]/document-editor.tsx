"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE, UserDocument } from "@/lib/api";

export function DocumentEditor({ document }: { document: UserDocument }) {
  const router = useRouter();
  const [title, setTitle] = useState(document.title);
  const [content, setContent] = useState(document.content_md);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    await fetch(`${API_BASE}/documents/${document.id}`, {
      method: "PATCH",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({ title, content_md: content }),
    });
    setBusy(false);
    router.refresh();
  }

  async function approve() {
    await save();
    await fetch(`${API_BASE}/documents/${document.id}/approve`, { method: "POST" });
    router.refresh();
  }

  async function send() {
    await save();
    await fetch(`${API_BASE}/documents/${document.id}/send`, { method: "POST" });
    router.refresh();
  }

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-sm text-zinc-500">{document.document_type} · {document.status}</div>
        <div className="flex gap-2">
          <Button type="button" onClick={save} disabled={busy}>{busy ? "保存中" : "保存"}</Button>
          <Button type="button" className="bg-emerald-700 hover:bg-emerald-600" onClick={approve}>审核通过</Button>
          <Button type="button" className="bg-zinc-900 hover:bg-zinc-800" onClick={send}>确认发送</Button>
          {document.file_path && (
            <a
              className="inline-flex h-9 items-center justify-center rounded-md border border-zinc-300 px-3 text-sm"
              href={`${API_BASE}/documents/${document.id}/download`}
            >
              下载 docx
            </a>
          )}
        </div>
      </div>
      <Input className="mb-3" value={title} onChange={(event) => setTitle(event.target.value)} />
      <Textarea className="min-h-72 font-mono" value={content} onChange={(event) => setContent(event.target.value)} />
    </section>
  );
}
