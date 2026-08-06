"use client";

import { useEffect, useRef, useState } from "react";
import { Message } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";

function ChatImage({ messageId, alt }: { messageId: number; alt: string }) {
  const [failed, setFailed] = useState(false);
  const url = `/api/backend/messages/${messageId}/image`;
  if (failed) {
    // 图片缓存被清理(超过保留期)或暂时取不到时的兜底，避免破图图标
    return (
      <div className="max-w-full rounded border border-slate-200 bg-slate-50 px-3 py-4 text-center text-xs text-slate-400 sm:max-w-sm">
        图片已过期或无法加载
      </div>
    );
  }
  return (
    <div className="relative max-w-full overflow-hidden rounded border border-slate-200 bg-slate-50 sm:max-w-sm">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={alt}
        className="max-h-60 max-w-full object-contain cursor-zoom-in rounded hover:opacity-90 transition-opacity"
        onClick={() => window.open(url, "_blank")}
        onError={() => setFailed(true)}
      />
    </div>
  );
}

export function ChatMessages({
  serviceCaseId,
  messages,
  userName,
  totalCount,
  pageSize = 50,
}: {
  serviceCaseId: number;
  messages: Message[];
  userName?: string | null;
  totalCount?: number | null;
  pageSize?: number;
}) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const didInitialScroll = useRef(false);
  const [older, setOlder] = useState<Message[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [exhausted, setExhausted] = useState(false);

  // 服务端已给最新一批(升序)，older 累积更早的历史，拼成完整升序列表
  const allMessages = [...older, ...messages];
  const loadedCount = allMessages.length;
  const hasMore =
    !exhausted && typeof totalCount === "number" && totalCount > loadedCount;
  const truncated = typeof totalCount === "number" && totalCount > loadedCount;

  // 仅首次挂载时滚到底；加载更早历史(向上追加)不再抢滚动
  useEffect(() => {
    const list = listRef.current;
    if (!list || didInitialScroll.current) return;
    list.scrollTop = list.scrollHeight;
    didInitialScroll.current = true;
  }, []);

  async function handleLoadMore() {
    if (loadingMore || allMessages.length === 0) return;
    setLoadingMore(true);
    const list = listRef.current;
    const prevScrollHeight = list?.scrollHeight ?? 0;
    try {
      const oldestId = allMessages[0].id;
      const res = await fetch(
        `/api/backend/service-cases/${serviceCaseId}/messages?limit=${pageSize}&before_id=${oldestId}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`load more failed: ${res.status}`);
      const batch: Message[] = await res.json();
      if (batch.length < pageSize) setExhausted(true);
      if (batch.length > 0) {
        setOlder((prev) => [...batch, ...prev]);
        // 追加到顶部后保持视口停在原来的位置，避免跳动
        requestAnimationFrame(() => {
          if (list) list.scrollTop = list.scrollHeight - prevScrollHeight;
        });
      }
    } catch (error) {
      console.error(error);
    } finally {
      setLoadingMore(false);
    }
  }

  if (messages.length === 0) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="font-medium">聊天记录</h2>
        <p className="mt-3 text-sm text-slate-500">暂无聊天记录</p>
      </section>
    );
  }

  const displayMessages = allMessages;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-medium">聊天记录</h2>
        <span className="text-sm text-slate-500">
          {truncated ? `已加载 ${loadedCount} / 共 ${totalCount} 条` : `${loadedCount} 条`}
        </span>
      </div>
      <div ref={listRef} className="mt-3 max-h-[480px] overflow-y-auto space-y-3 pr-1">
        {hasMore && (
          <div className="flex justify-center pb-1">
            <button
              type="button"
              onClick={handleLoadMore}
              disabled={loadingMore}
              className="rounded-full border border-slate-200 bg-slate-50 px-4 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loadingMore ? "加载中…" : "加载更早的记录"}
            </button>
          </div>
        )}
        {displayMessages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.direction === "outbound" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[92%] rounded-lg px-3 py-2 text-sm sm:max-w-[80%] ${
                msg.direction === "outbound"
                  ? "bg-blue-50 text-blue-900"
                  : msg.sender_type === "bot"
                  ? "bg-green-50 text-green-900"
                  : msg.sender_type === "staff"
                  ? "bg-purple-50 text-purple-900"
                  : "bg-slate-100 text-slate-900"
              }`}
            >
              <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs opacity-70">
                <span>
                  {msg.direction === "outbound"
                    ? msg.sender_type === "bot"
                      ? "燃人.AI"
                      : "运营"
                    : msg.sender_type === "staff"
                    ? msg.display_sender_name || "班主任"
                    : msg.display_sender_name || userName || "客户"}
                  {msg.direction === "inbound" && msg.sender_type === "staff" && (
                    <span className="ml-1 rounded bg-purple-100 px-1 text-[10px] text-purple-700">班主任</span>
                  )}
                  {msg.direction === "inbound" &&
                    msg.analysis_json &&
                    typeof msg.analysis_json === "object" &&
                    (msg.analysis_json as any)._qiwe_meta?.sender_id && (
                      <span className="text-[10px] text-slate-400 select-all ml-1">
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
                {msg.direction === "inbound" &&
                  msg.analysis_json &&
                  typeof msg.analysis_json === "object" &&
                  (msg.analysis_json as any)._qiwe_meta?.voice_transcript && (
                    <span className="rounded bg-indigo-100 px-1 text-indigo-700">
                      语音
                    </span>
                  )}
              </div>
              {msg.message_type === "image" ? (
                <div className="mt-2 space-y-2">
                  <ChatImage messageId={msg.id} alt={msg.content || "用户上传的图片"} />
                  {msg.content && msg.content !== "[图片]" && (
                    <p className="rounded border border-slate-200 bg-slate-50/50 p-2 text-xs italic text-slate-600">
                      AI描述: {msg.content}
                    </p>
                  )}
                </div>
              ) : (
                <div className="text-break whitespace-pre-wrap">{msg.content}</div>
              )}
              {msg.status === "failed" &&
                (msg.error?.includes("驳回") ? (
                  <div className="mt-1 text-xs text-slate-400">已驳回 · 未发送</div>
                ) : msg.error?.includes("风控") ? (
                  <div className="mt-1 text-xs text-red-500" title={msg.error}>
                    发送失败 · 企业微信风控拦截（账号违规被限制，实际未进群）
                  </div>
                ) : (
                  <div className="mt-1 text-xs text-red-500">发送失败</div>
                ))}
              {msg.direction === "outbound" && msg.status === "pending" && (
                <div className="mt-1 text-xs font-medium text-slate-600">
                  等待发送
                </div>
              )}
              {msg.direction === "outbound" && msg.status === "sending" && (
                <div className="mt-1 text-xs font-medium text-cyan-800">
                  正在发送
                </div>
              )}
              {msg.direction === "outbound" && msg.status === "unknown" && (
                <div
                  className="mt-1 text-xs font-semibold text-amber-800"
                  title={msg.error || "渠道请求已开始，但未取得可信发送结果"}
                >
                  发送结果待确认 · 未自动重发
                </div>
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
