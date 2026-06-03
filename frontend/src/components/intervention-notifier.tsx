"use client";

import { useSSE } from "@/hooks/use-sse";

export function InterventionBadge() {
  const { pendingReviewCount, interventionCount } = useSSE();
  const count = pendingReviewCount + interventionCount;

  if (count === 0) return null;
  return (
    <span className="absolute -right-4 -top-2 h-2.5 w-2.5 rounded-full bg-red-500" />
  );
}
