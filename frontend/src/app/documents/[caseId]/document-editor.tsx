"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { API_BASE, UserDocument } from "@/lib/api";
import { MarkdownPreview } from "@/components/markdown-preview";

export function DocumentEditor({ document }: { document: UserDocument }) {
  const router = useRouter();
  const [content, setContent] = useState(document.content_md);
  const [isEditing, setIsEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const save = useCallback(async (newContent: string) => {
    setSaving(true);
    await fetch(`${API_BASE}/documents/${document.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content_md: newContent }),
    });
    setSaving(false);
  }, [document.id]);

  useEffect(() => {
    if (isEditing) {
      const timer = setTimeout(() => {
        save(content);
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [content, isEditing, save]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = textareaRef.current.scrollHeight + "px";
    }
  }, [content, isEditing]);

  async function send() {
    setBusy(true);
    await save(content);
    await fetch(`${API_BASE}/documents/${document.id}/send`, { method: "POST" });
    setBusy(false);
    router.refresh();
  }

  return (
    <section className="rounded-xl border border-zinc-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-zinc-100 px-6 py-4">
        <div>
          <div className="text-sm text-zinc-500">{document.document_type}</div>
          {saving && <span className="text-xs text-zinc-400 ml-2">保存中...</span>}
        </div>
        <div className="flex gap-2">
          <Button type="button" className="bg-emerald-700 hover:bg-emerald-600" onClick={send} disabled={busy}>
            {busy ? "发送中..." : "确认发送"}
          </Button>
          {document.file_path && (
            <a
              className="inline-flex h-9 items-center justify-center rounded-md border border-zinc-300 px-3 text-sm hover:bg-zinc-50"
              href={`${API_BASE}/documents/${document.id}/download`}
            >
              下载 docx
            </a>
          )}
        </div>
      </div>

      <div className="p-6">
        {isEditing ? (
          <div>
            <div className="mb-2 text-xs text-zinc-400">Markdown 编辑模式（修改后自动保存）</div>
            <textarea
              ref={textareaRef}
              className="w-full min-h-72 font-mono text-sm border border-zinc-200 rounded-lg p-4 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none overflow-hidden"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              onBlur={() => setIsEditing(false)}
              autoFocus
            />
          </div>
        ) : (
          <div 
            className="min-h-72 rounded-lg border border-transparent hover:border-zinc-200 p-4 cursor-text transition-colors"
            onClick={() => setIsEditing(true)}
          >
            <MarkdownPreview content={content} />
            <div className="mt-4 text-xs text-zinc-400 text-center">点击任意位置编辑</div>
          </div>
        )}
      </div>
    </section>
  );
}
