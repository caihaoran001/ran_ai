"use client";

import { Search, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

export function CaseSearchBox() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [value, setValue] = useState(searchParams.get("q") ?? "");

  const navigate = (q: string) => {
    const params = new URLSearchParams();
    const filter = searchParams.get("filter");
    if (filter) params.set("filter", filter);
    if (q.trim()) params.set("q", q.trim());
    const qs = params.toString();
    router.push(`/users${qs ? `?${qs}` : ""}`);
  };

  return (
    <div className="flex items-center gap-2">
      <div className="relative flex-1">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") navigate(value);
          }}
          placeholder="搜索 姓名 / 手机号 / 群名"
          className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-9 text-sm shadow-sm outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-200"
        />
        {value && (
          <button
            type="button"
            onClick={() => {
              setValue("");
              navigate("");
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            aria-label="清除搜索"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={() => navigate(value)}
        className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-cyan-800"
      >
        搜索
      </button>
    </div>
  );
}
