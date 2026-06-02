"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const POLL_INTERVAL_MS = 15_000;

interface PendingCountResponse {
  count: number;
}

export function InterventionBadge() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let isMounted = true;

    async function load() {
      // Query all pending review items from the review console
      const [pendingMessages, pendingDrafts] = await Promise.all([
        api<unknown[]>("/messages/pending-ai").catch(() => []),
        api<unknown[]>("/knowledge-drafts?status=pending").catch(() => []),
      ]);

      if (!isMounted) return;
      setCount(pendingMessages.length + pendingDrafts.length);
    }

    void load();
    const timer = window.setInterval(load, POLL_INTERVAL_MS);
    return () => {
      isMounted = false;
      window.clearInterval(timer);
    };
  }, []);

  if (count === 0) return null;
  return (
    <span className="absolute -right-4 -top-2 min-w-[18px] h-[18px] rounded-full bg-red-500 text-white text-[10px] flex items-center justify-center font-bold">
      {count > 99 ? "99+" : count}
    </span>
  );
}
