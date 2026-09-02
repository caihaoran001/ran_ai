"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, Pencil, Check, X, ChevronDown, Globe } from "lucide-react";
import { API_BASE } from "@/lib/api";

const OPTIONS: { value: "zh" | "en"; label: string; badgeClass: string }[] = [
  { value: "zh", label: "中文 (zh)", badgeClass: "border-slate-200 bg-slate-50 text-slate-700" },
  { value: "en", label: "English (en)", badgeClass: "border-blue-200 bg-blue-50 text-blue-700" },
];

export function CaseLanguage({
  caseId,
  language,
}: {
  caseId: number;
  language: "zh" | "en";
}) {
  const initialValue = language === "en" ? "en" : "zh";
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<"zh" | "en">(initialValue);
  const [busy, setBusy] = useState(false);
  const [isRefreshing, startTransition] = useTransition();
  const pending = busy || isRefreshing;
  const router = useRouter();

  async function save() {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/service-cases/${caseId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ language: value }),
      });
      if (res.ok) {
        setEditing(false);
        startTransition(() => router.refresh());
      } else {
        alert("操作失败：" + (await res.text()));
      }
    } catch (err) {
      alert("操作出错");
      console.error(err);
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setEditing(false);
    setValue(initialValue);
  }

  const currentOption = OPTIONS.find((opt) => opt.value === (editing ? value : initialValue)) ?? OPTIONS[0];

  return (
    <div className="inline-flex items-center gap-1">
      {editing ? (
        <div className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white p-0.5 shadow-sm">
          <div className="relative inline-flex items-center">
            <select
              value={value}
              onChange={(e) => setValue(e.target.value as "zh" | "en")}
              disabled={pending}
              className="appearance-none rounded border-0 bg-transparent py-0.5 pl-2 pr-6 text-xs font-medium text-slate-800 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              {OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-1 h-3 w-3 text-slate-400" />
          </div>
          <button
            type="button"
            onClick={save}
            disabled={pending}
            className="inline-flex h-5 w-5 items-center justify-center rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            title="保存"
          >
            {pending ? (
              <LoaderCircle className="h-3 w-3 animate-spin" />
            ) : (
              <Check className="h-3 w-3" />
            )}
          </button>
          <button
            type="button"
            onClick={cancel}
            disabled={pending}
            className="inline-flex h-5 w-5 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            title="取消"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      ) : (
        <span
          className={`group status-pill inline-flex cursor-pointer items-center gap-1.5 transition hover:brightness-95 ${currentOption.badgeClass}`}
          onClick={() => setEditing(true)}
          title="点击切换语种"
        >
          <Globe className="h-3.5 w-3.5 opacity-70" />
          <span>{currentOption.label}</span>
          <Pencil className="h-3 w-3 opacity-0 transition group-hover:opacity-60" />
        </span>
      )}
    </div>
  );
}
