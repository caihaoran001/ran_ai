"use client";

import { useState } from "react";
import { Message } from "@/lib/api";

export function ChatMessages({ messages }: { messages: Message[] }) {
  const [expanded, setExpanded] = useState(false);

  if (messages.length === 0) {
    return (
      <section className="rounded-lg border border-zinc-200 bg-white p-4">
        <h2 className="font-medium">聊天记录</h2>
        <p className="mt-3 text-sm text-zinc-500">暂无聊天记录</p>
      </section>
    );
  }

  const displayMessages = expanded ? messages : messages.slice(0, 10);

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">聊天记录</h2>
        <span className="text-sm text-zinc-500">{messages.length} 条</span>
      </div>
      <div className="mt-3 space-y-3">
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
                      ? "机器人"
                      : "运营"
                    : "用户"}
                </span>
                <span>{formatTime(msg.created_at)}</span>
                {msg.purpose === "checkin" && (
                  <span className="rounded bg-yellow-100 px-1 text-yellow-700">打卡</span>
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
      {messages.length > 10 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-3 w-full rounded-md border border-zinc-200 py-2 text-sm text-zinc-600 hover:bg-zinc-50"
        >
          {expanded ? "收起" : `展开全部 ${messages.length} 条`}
        </button>
      )}
    </section>
  );
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
