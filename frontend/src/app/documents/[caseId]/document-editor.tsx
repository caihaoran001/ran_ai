"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE, DocumentContentJson, UserDocument } from "@/lib/api";
import { MarkdownPreview } from "@/components/markdown-preview";

export function DocumentEditor({ document }: { document: UserDocument }) {
  const router = useRouter();
  const [content, setContent] = useState(document.content_md);
  const [contentJson, setContentJson] = useState<DocumentContentJson | null>(document.content_json ?? null);
  const [isEditing, setIsEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [sent, setSent] = useState(document.status === "sent");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const save = useCallback(async (newContent: string, newContentJson?: DocumentContentJson | null) => {
    setSaving(true);
    await fetch(`${API_BASE}/documents/${document.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(newContentJson ? { content_json: newContentJson } : { content_md: newContent }),
    });
    setSaving(false);
  }, [document.id]);

  useEffect(() => {
    if (isEditing && !contentJson) {
      const timer = setTimeout(() => {
        save(content);
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [content, contentJson, isEditing, save]);

  useEffect(() => {
    if (!contentJson) return;
    const timer = setTimeout(() => {
      save(content, contentJson);
    }, 1200);
    return () => clearTimeout(timer);
  }, [content, contentJson, save]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = textareaRef.current.scrollHeight + "px";
    }
  }, [content, isEditing]);

  async function send() {
    setBusy(true);
    await save(content, contentJson);
    await fetch(`${API_BASE}/documents/${document.id}/send`, { method: "POST" });
    setSent(true);
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
          <Button
            type="button"
            className={sent ? "bg-zinc-100 text-zinc-400 border border-zinc-200 cursor-not-allowed hover:bg-zinc-100" : "bg-emerald-700 hover:bg-emerald-600 text-white"}
            onClick={send}
            disabled={busy || sent}
          >
            {busy ? "发送中..." : sent ? "已发送" : "确认发送"}
          </Button>
          <a
            suppressHydrationWarning
            className="inline-flex h-9 items-center justify-center rounded-md border border-zinc-300 px-3 text-sm hover:bg-zinc-50"
            href={`${API_BASE}/documents/${document.id}/download`}
          >
            下载 docx
          </a>
        </div>
      </div>

      <div className="p-6">
        {contentJson ? (
          <StructuredEditor
            value={contentJson}
            onChange={(next) => {
              setContentJson(next);
              setContent(renderPreviewText(next));
            }}
          />
        ) : isEditing ? (
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

function StructuredEditor({
  value,
  onChange,
}: {
  value: DocumentContentJson;
  onChange: (value: DocumentContentJson) => void;
}) {
  function updateBlock(blockIndex: number, updater: (block: DocumentContentJson["blocks"][number]) => DocumentContentJson["blocks"][number]) {
    onChange({
      ...value,
      blocks: value.blocks.map((block, index) => (index === blockIndex ? updater(block) : block)),
    });
  }

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-4">
        <div className="text-sm text-zinc-500">模板标题</div>
        <div className="mt-1 font-semibold text-zinc-900">{value.title}</div>
      </div>
      {value.blocks.map((block, blockIndex) => (
        <section key={block.key || blockIndex} className="rounded-lg border border-zinc-200 p-4">
          <h3 className="font-semibold text-zinc-900">{block.title}</h3>
          {block.kind === "table" ? (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse text-sm">
                <tbody>
                  {(block.rows ?? []).map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex} className="border border-zinc-200 p-2 align-top">
                          {cell.editable === false ? (
                            <div className="font-medium text-zinc-700">{cell.value}</div>
                          ) : (
                            <Textarea
                              className="min-h-12 border-0 p-0 focus:border-0"
                              value={cell.value ?? ""}
                              onChange={(event) =>
                                updateBlock(blockIndex, (current) => ({
                                  ...current,
                                  rows: (current.rows ?? []).map((currentRow, currentRowIndex) =>
                                    currentRowIndex === rowIndex
                                      ? currentRow.map((currentCell, currentCellIndex) =>
                                          currentCellIndex === cellIndex
                                            ? { ...currentCell, value: event.target.value }
                                            : currentCell,
                                        )
                                      : currentRow,
                                  ),
                                }))
                              }
                            />
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : block.kind === "list" ? (
            <div className="mt-3 space-y-2">
              {(block.items ?? []).map((item, itemIndex) => (
                <Textarea
                  key={itemIndex}
                  className="min-h-16"
                  value={item}
                  onChange={(event) =>
                    updateBlock(blockIndex, (current) => ({
                      ...current,
                      items: (current.items ?? []).map((currentItem, currentItemIndex) =>
                        currentItemIndex === itemIndex ? event.target.value : currentItem,
                      ),
                    }))
                  }
                />
              ))}
              <Button
                type="button"
                className="bg-white text-zinc-700 ring-1 ring-zinc-200 hover:bg-zinc-50"
                onClick={() =>
                  updateBlock(blockIndex, (current) => ({
                    ...current,
                    items: [...(current.items ?? []), ""],
                  }))
                }
              >
                添加一条
              </Button>
            </div>
          ) : (
            <Textarea
              className="mt-3 min-h-28"
              value={block.content ?? ""}
              onChange={(event) =>
                updateBlock(blockIndex, (current) => ({
                  ...current,
                  content: event.target.value,
                }))
              }
            />
          )}
        </section>
      ))}
      <div className="text-center text-xs text-zinc-400">内容会自动保存并重新生成 docx；标题和表格框架保持固定</div>
    </div>
  );
}

function renderPreviewText(value: DocumentContentJson) {
  const lines = [`# ${value.title}`];
  for (const block of value.blocks) {
    if (block.display === "inline") {
      lines.push(`${block.title}：${block.content ?? ""}`);
      continue;
    }
    lines.push("", `## ${block.title}`);
    if (block.kind === "table") {
      for (const row of block.rows ?? []) {
        lines.push(row.map((cell) => cell.value).join(" | "));
      }
    } else if (block.kind === "list") {
      for (const item of block.items ?? []) {
        lines.push(`- ${item}`);
      }
    } else {
      lines.push(block.content ?? "");
    }
  }
  return lines.join("\n");
}
