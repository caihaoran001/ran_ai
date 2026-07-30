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
  const [confirming, setConfirming] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const save = useCallback(async (newContent: string, newContentJson?: DocumentContentJson | null) => {
    setSaving(true);
    try {
      const response = await fetch(`${API_BASE}/documents/${document.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(newContentJson ? { content_json: newContentJson } : { content_md: newContent }),
      });
      return response.ok;
    } catch {
      return false;
    } finally {
      setSaving(false);
    }
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
    setConfirming(false);
    setSendError(null);
    setBusy(true);
    try {
      const saved = await save(content, contentJson);
      if (!saved) {
        throw new Error("保存失败，未发送。请稍后重试。");
      }
      const response = await fetch(`${API_BASE}/documents/${document.id}/send`, { method: "POST" });
      if (!response.ok) {
        throw new Error(await response.text() || "发送失败，请稍后重试。");
      }
      setSent(true);
      router.refresh();
    } catch (error) {
      setSendError(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  // 已发送的文件允许重发（企微「已发送」仅代表网关受理，不等于真正进群），重发前二次确认防误点
  function handleSendClick() {
    if (sent) {
      setConfirming(true);
    } else {
      send();
    }
  }

  // 内部复核标记只存在于 content_json 顶层元数据，不在用户可见的 blocks / content_md 里。
  const internalReview = contentJson?.internal_review ?? document.content_json?.internal_review;
  const manualReviewRequired = Boolean(internalReview?.manual_review_required);
  const manualReviewReasons = internalReview?.reasons ?? [];

  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
      {manualReviewRequired && (
        <div className="rounded-t-xl border-b border-rose-200 bg-rose-50 px-6 py-4">
          <p className="text-sm font-bold text-rose-900">需人工复核，请检查个案方案后发送</p>
          <p className="mt-1 text-xs leading-relaxed text-rose-800">
            系统已阻止这份入营文件自动发送。请确认安全边界、产品排班与目标设定无误（必要时直接在下方编辑），再点击「确认发送」。
          </p>
          {manualReviewReasons.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-xs font-medium text-rose-800">
              {manualReviewReasons.map((reason) => (
                <li key={reason}>· {reason}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
        <div>
          <div className="text-sm text-slate-500">{document.document_type}</div>
          {saving && <span className="text-xs text-slate-400 ml-2">保存中...</span>}
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            className={
              "h-9 min-h-0 rounded-md px-3 " +
              (sent
                ? "border border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100"
                : "bg-emerald-700 hover:bg-emerald-600 text-white")
            }
            onClick={handleSendClick}
            loading={busy}
            loadingText="发送中…"
          >
            {sent ? "重新发送" : "确认发送"}
          </Button>
          <a
            suppressHydrationWarning
            className="inline-flex h-9 items-center justify-center rounded-md border border-slate-300 px-3 text-sm hover:bg-slate-50"
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
            <div className="mb-2 text-xs text-slate-400">Markdown 编辑模式（修改后自动保存）</div>
            <textarea
              ref={textareaRef}
              className="w-full min-h-72 font-mono text-sm border border-slate-200 rounded-lg p-4 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none overflow-hidden"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              onBlur={() => setIsEditing(false)}
              autoFocus
            />
          </div>
        ) : (
          <div 
            className="min-h-72 rounded-lg border border-transparent hover:border-slate-200 p-4 cursor-text transition-colors"
            onClick={() => setIsEditing(true)}
          >
            <MarkdownPreview content={content} />
            <div className="mt-4 text-xs text-slate-400 text-center">点击任意位置编辑</div>
          </div>
        )}
        {sendError && (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {sendError}
          </div>
        )}
      </div>

      {confirming && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm"
          onClick={() => setConfirming(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-slate-900">重新发送到群？</h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-600">
              将把《{document.title}》再次发送到该服务单的群里。若上次未真正到群可用此操作补发，请确认无误后再发。
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                className="inline-flex h-9 items-center justify-center rounded-md border border-slate-300 px-4 text-sm text-slate-600 hover:bg-slate-50"
                onClick={() => setConfirming(false)}
              >
                取消
              </button>
              <button
                type="button"
                className="inline-flex h-9 items-center justify-center rounded-md bg-emerald-700 px-4 text-sm font-semibold text-white hover:bg-emerald-600"
                onClick={send}
              >
                确认重新发送
              </button>
            </div>
          </div>
        </div>
      )}
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
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
        <div className="text-sm text-slate-500">模板标题</div>
        <div className="mt-1 text-center font-semibold text-slate-900">{value.title}</div>
      </div>
      {value.blocks.map((block, blockIndex) => (
        <section key={block.key || blockIndex} className="rounded-lg border border-slate-200 p-4">
          <h3 className="font-semibold text-slate-900">{block.title}</h3>
          {block.kind === "table" ? (
            <div className="mt-3 overflow-x-auto">
              <table className={`w-full min-w-[640px] border-collapse text-sm${execColWidths(block.rows) ? " table-fixed" : ""}`}>
                {execColWidths(block.rows) && (
                  <colgroup>
                    {execColWidths(block.rows)!.map((w, i) => (
                      <col key={i} style={{ width: w }} />
                    ))}
                  </colgroup>
                )}
                <tbody>
                  {(block.rows ?? []).map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex} className="border border-slate-200 p-2 align-top">
                          {cell.editable === false ? (
                            <div className="font-medium text-slate-700">{cell.value}</div>
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
                <div key={itemIndex} className="flex items-start gap-2">
                  <Textarea
                    className="min-h-16 flex-1"
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
                  <button
                    type="button"
                    title="删除这一条"
                    className="mt-1 shrink-0 rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 hover:border-red-300 hover:text-red-600"
                    onClick={() =>
                      updateBlock(blockIndex, (current) => ({
                        ...current,
                        items: (current.items ?? []).filter((_, i) => i !== itemIndex),
                      }))
                    }
                  >
                    删除
                  </button>
                </div>
              ))}
              <Button
                type="button"
                className="bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50"
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
      <div className="text-center text-xs text-slate-400">内容会自动保存并重新生成 docx；标题和表格框架保持固定</div>
    </div>
  );
}

function execColWidths(rows?: Array<Array<{ value?: string | null }>>): string[] | null {
  const header = rows?.[0];
  if (!header) return null;
  const texts = header.map((cell) => String(cell?.value ?? "").trim());
  if (!texts.includes("执行标准")) return null;
  const weightMap: Record<string, number> = { 周次: 1.1, 重点: 2.2, 执行标准: 3.7 };
  const weights = texts.map((text) => weightMap[text] ?? 2.0);
  const total = weights.reduce((sum, weight) => sum + weight, 0) || 1;
  return weights.map((weight) => `${((weight / total) * 100).toFixed(1)}%`);
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

function readableError(error: unknown): string {
  const message = error instanceof Error ? error.message : "发送失败，请稍后重试。";
  try {
    const parsed = JSON.parse(message) as { detail?: unknown };
    return typeof parsed.detail === "string" ? parsed.detail : message;
  } catch {
    return message;
  }
}
