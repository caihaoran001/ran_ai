"use client";

import { useSSE } from "@/hooks/use-sse";

export function InterventionBadge() {
  // pendingReviewCount(=pending_total) 只含待审核文件、知识草稿和已退款待确认。
  const { pendingReviewCount } = useSSE();
  const count = pendingReviewCount;

  if (count === 0) return null;
  return (
    <span className="absolute -right-4 -top-2 h-2.5 w-2.5 rounded-full bg-red-500" />
  );
}
