"use client";

import { useEffect, useState } from "react";
import { api, ServiceCase } from "@/lib/api";

const POLL_INTERVAL_MS = 15_000;

export function InterventionBadge() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let isMounted = true;
    async function load() {
      const cases = await api<ServiceCase[]>("/service-cases?filter=human_intervention").catch(() => []);
      if (isMounted) setCount(cases.length);
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
    <span className="absolute -right-2 -top-2 min-w-[18px] h-[18px] rounded-full bg-red-500 text-white text-[10px] flex items-center justify-center font-bold">
      {count > 99 ? "99+" : count}
    </span>
  );
}
