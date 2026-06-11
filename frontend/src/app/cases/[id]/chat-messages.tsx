"use client";

import { useEffect, useRef, useState } from "react";
import { Message } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";

export function ChatMessages({ messages, userName }: { messages: Message[]; userName?: string | null }) {
  const [expanded, setExpanded] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    list.scrollTop = list.scrollHeight;
  }, [messages.length]);

  if (messages.length === 0) {
    return (
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <h2 className="font-medium">聊天记录</h2>
        <p className="mt-3 text-sm text-zinc-500">暂无聊天记录</p>
      </section>
    );
  }

  const displayMessages = messages;

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">聊天记录</h2>
        <span className="text-sm text-zinc-500">{messages.length} 条</span>
      </div>
      <div ref={listRef} className="mt-3 max-h-[480px] overflow-y-auto space-y-3 pr-1">
        {displayMessages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.direction === "outbound" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.direction === "outbound"
                  ? "bg-blue-50 text-blue-900"
                  : msg.sender_type === "bot"
                  ? "bg-green-50 text-green-900"
                  : "bg-zinc-100 text-zinc-900"
              }`}
            >
              <div className="flex items-center gap-2 text-xs opacity-70 mb-1">
                <span>
                  {msg.direction === "outbound"
                    ? msg.sender_type === "bot"
                      ? "燃人.AI"
                      : "运营"
                    : msg.display_sender_name || userName || "用户"}
                  {msg.direction === "inbound" &&
                    msg.analysis_json &&
                    typeof msg.analysis_json === "object" &&
                    (msg.analysis_json as any)._qiwe_meta?.sender_id && (
                      <span className="text-[10px] text-zinc-400 select-all ml-1">
                        (ID: {(msg.analysis_json as any)._qiwe_meta.sender_id})
                      </span>
                    )}
                </span>
                <span>{formatTime(msg.display_at ?? msg.created_at)}</span>
                {msg.purpose === "checkin" && (
                  <span className="rounded bg-yellow-100 px-1 text-yellow-700">打卡</span>
                )}
                {msg.purpose === "document_delivery" && (
                  <span className="rounded bg-blue-100 px-1 text-blue-700">文件</span>
                )}
              </div>
              <div className="whitespace-pre-wrap">{msg.content}</div>
              {msg.status === "failed" && (
                <div className="mt-1 text-xs text-red-500">发送失败</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function formatTime(value: string) {
  return formatChinaDateTime(value);
}
