"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Check, Globe, LoaderCircle } from "lucide-react";
import { API_BASE } from "@/lib/api";

type Lang = "zh" | "en";

const OPTIONS: { value: Lang; label: string; hint: string }[] = [
  { value: "zh", label: "中文", hint: "服务语种切换为中文" },
  { value: "en", label: "English", hint: "服务语种切换为英文" },
];

export function CaseLanguage({
  caseId,
  language,
}: {
  caseId: number;
  language: Lang;
}) {
  const serverValue: Lang = language === "en" ? "en" : "zh";
  const [value, setValue] = useState<Lang>(serverValue);
  const [synced, setSynced] = useState<Lang>(serverValue);
  const [busy, setBusy] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, startTransition] = useTransition();
  const router = useRouter();
  const pending = busy || isRefreshing;

  // 服务端数据刷新后（含轮询刷新）把本地乐观值对齐回真实值；保存过程中不打断动画。
  if (!pending && serverValue !== synced) {
    setSynced(serverValue);
    setValue(serverValue);
  }

  async function select(next: Lang) {
    if (pending || next === value) return;
    const previous = value;
    setValue(next); // 乐观切换：滑块立刻动，不等接口
    setError(null);
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/service-cases/${caseId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ language: next }),
      });
      if (!res.ok) {
        setValue(previous);
        setError((await res.text()) || "保存失败");
        return;
      }
      setJustSaved(true);
      window.setTimeout(() => setJustSaved(false), 1400);
      startTransition(() => router.refresh());
    } catch (err) {
      setValue(previous);
      setError("网络异常，未保存");
      console.error(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      <span
        role="radiogroup"
        aria-label="服务语种"
        aria-busy={pending || undefined}
        className={`group/lang relative inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-100/80 py-0.5 pl-2 pr-0.5 transition-all duration-200 hover:border-slate-300 ${
          pending ? "opacity-80" : ""
        }`}
        style={{ boxShadow: "var(--shadow-xs)" }}
      >
        <span className="relative inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center">
          <Globe
            aria-hidden="true"
            className={`absolute h-3.5 w-3.5 text-slate-400 transition-all duration-200 ${
              pending || justSaved ? "scale-50 opacity-0" : "scale-100 opacity-100"
            }`}
          />
          <LoaderCircle
            aria-hidden="true"
            className={`absolute h-3.5 w-3.5 animate-spin text-cyan-600 transition-all duration-200 motion-reduce:animate-none ${
              pending ? "scale-100 opacity-100" : "scale-50 opacity-0"
            }`}
          />
          <Check
            aria-hidden="true"
            className={`absolute h-3.5 w-3.5 text-emerald-600 transition-all duration-200 ${
              justSaved && !pending ? "scale-100 opacity-100" : "scale-50 opacity-0"
            }`}
          />
        </span>

        <span className="relative inline-grid grid-cols-2">
          {/* 滑块：跟随选中项左右滑动 */}
          <span
            aria-hidden="true"
            className={`pointer-events-none absolute inset-y-0 left-0 w-1/2 rounded-full border border-white/70 bg-white transition-transform duration-300 will-change-transform ${
              value === "en" ? "translate-x-full" : "translate-x-0"
            }`}
            style={{
              boxShadow: "var(--shadow-sm)",
              transitionTimingFunction: "var(--ease-out)",
            }}
          />
          {OPTIONS.map((option) => {
            const active = value === option.value;
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={active}
                title={option.hint}
                disabled={pending}
                onClick={() => select(option.value)}
                className={`relative z-10 rounded-full px-2.5 py-0.5 text-xs font-semibold leading-4 outline-none transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-cyan-400 focus-visible:ring-offset-1 disabled:cursor-wait ${
                  active
                    ? "text-cyan-800"
                    : "text-slate-500 hover:text-slate-800"
                }`}
              >
                {option.label}
              </button>
            );
          })}
        </span>
      </span>

      {error && (
        <span className="animate-fade-in-up text-xs font-semibold text-rose-600">
          {error}
        </span>
      )}
    </span>
  );
}
