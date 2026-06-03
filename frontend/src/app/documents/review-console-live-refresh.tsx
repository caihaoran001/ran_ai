"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useSSE } from "@/hooks/use-sse";

export function ReviewConsoleLiveRefresh() {
  const router = useRouter();
  const { pendingReviewCount, interventionCount, connected } = useSSE();
  const lastSignature = useRef<string | null>(null);

  useEffect(() => {
    if (!connected) return;
    const signature = `${pendingReviewCount}:${interventionCount}`;
    if (lastSignature.current === null) {
      lastSignature.current = signature;
      return;
    }
    if (lastSignature.current !== signature) {
      lastSignature.current = signature;
      router.refresh();
    }
  }, [connected, interventionCount, pendingReviewCount, router]);

  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-emerald-100 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
      <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-emerald-500" : "bg-zinc-300"}`} />
      {connected ? "实时监听中" : "实时监听连接中"}
    </div>
  );
}
