"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, Pencil, Check, X, ChevronDown } from "lucide-react";
import { API_BASE } from "@/lib/api";
import {
  GroupTypeBadge,
  normalizeGroupType,
  type GroupType,
} from "@/components/group-type-badge";

const OPTIONS: { value: GroupType; label: string }[] = [
  { value: "new_group", label: "新群" },
  { value: "legacy", label: "老群" },
];

export function CaseGroupType({
  caseId,
  groupType,
}: {
  caseId: number;
  groupType: string;
}) {
  const initialValue = normalizeGroupType(groupType);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<GroupType>(initialValue);
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
        body: JSON.stringify({ group_type: value }),
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

  const displayValue = editing ? value : initialValue;

  return (
    <span className="inline-flex items-center gap-2">
      {editing ? (
        <span className="inline-flex items-center gap-2">
          <span className="relative inline-flex items-center">
            <select
              value={value}
              onChange={(event) => setValue(event.target.value as GroupType)}
              disabled={pending}
              className="appearance-none rounded-xl border border-slate-300 bg-white py-1.5 pl-3 pr-8 text-xs font-semibold text-slate-800 shadow-[var(--shadow-xs)] outline-none transition-all duration-150 hover:border-slate-400 focus:border-cyan-500 focus:ring-4 focus:ring-cyan-500/15 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500"
            >
              {OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <ChevronDown
              className="pointer-events-none absolute right-2.5 h-3.5 w-3.5 text-slate-400"
              aria-hidden="true"
            />
          </span>
          <button
            type="button"
            onClick={save}
            disabled={pending}
            aria-busy={pending || undefined}
            className="inline-flex h-7 items-center gap-1 rounded-lg border border-cyan-200 bg-cyan-50 px-2.5 text-xs font-semibold text-cyan-800 shadow-[var(--shadow-xs)] transition-all duration-150 hover:border-cyan-300 hover:bg-cyan-100 hover:text-cyan-900 focus-visible:ring-2 focus-visible:ring-cyan-400 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending ? (
              <LoaderCircle
                className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
                aria-hidden="true"
              />
            ) : (
              <Check className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {pending ? "保存中…" : "保存"}
          </button>
          <button
            type="button"
            onClick={cancel}
            disabled={pending}
            className="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-600 shadow-[var(--shadow-xs)] transition-all duration-150 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-800 focus-visible:ring-2 focus-visible:ring-slate-300 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            取消
          </button>
        </span>
      ) : (
        <span className="inline-flex items-center gap-2">
          <GroupTypeBadge groupType={displayValue} />
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="inline-flex h-7 items-center gap-1 rounded-lg border border-transparent bg-transparent px-2 text-xs font-semibold text-slate-500 outline-none transition-all duration-150 hover:border-slate-200 hover:bg-slate-100 hover:text-slate-800 focus-visible:border-cyan-300 focus-visible:bg-cyan-50 focus-visible:text-cyan-800"
            title="修改群类型"
          >
            <Pencil className="h-3 w-3" aria-hidden="true" />
            修改
          </button>
        </span>
      )}
    </span>
  );
}
