"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useSSE } from "@/hooks/use-sse";

export function ReviewConsoleLiveRefresh() {
  const router = useRouter();
  const { pendingReviewCount, connected } = useSSE();
  const lastSignature = useRef<string | null>(null);

  useEffect(() => {
    if (!connected) return;
    // pendingReviewCount(=pending_total) 已含待审核文件/知识草稿/已退款待确认/体重状态待跟进，
    // 任一变化即刷新控制台；AI 回复待审核不再参与。
    const signature = `${pendingReviewCount}`;
    if (lastSignature.current === null) {
      lastSignature.current = signature;
      return;
    }
    if (lastSignature.current !== signature) {
      lastSignature.current = signature;
      router.refresh();
    }
  }, [connected, pendingReviewCount, router]);

  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-emerald-100 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
      <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-emerald-500" : "bg-slate-300"}`} />
      {connected ? "实时监听中" : "实时监听连接中"}
    </div>
  );
}
