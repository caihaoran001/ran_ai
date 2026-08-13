"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle } from "lucide-react";
import { API_BASE } from "@/lib/api";

export function CaseGroupType({
  caseId,
  groupType,
}: {
  caseId: number;
  groupType: string;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(groupType === "new_group" ? "new_group" : "legacy");
  const [busy, setBusy] = useState(false);
  const [isRefreshing, startTransition] = useTransition();
  const pending = busy || isRefreshing;
  const router = useRouter();
  const isNewGroup = (editing ? value : groupType) === "new_group";

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

  return (
    <span className="inline-flex items-center gap-2">
      {editing ? (
        <span className="inline-flex items-center gap-1.5">
          <select
            value={value}
            onChange={(event) => setValue(event.target.value)}
            disabled={pending}
            className="rounded-md border border-slate-300 bg-white px-1.5 py-0.5 text-xs text-slate-800"
          >
            <option value="new_group">新群</option>
            <option value="legacy">老群</option>
          </select>
          <button
            type="button"
            onClick={save}
            disabled={pending}
            aria-busy={pending || undefined}
            className="inline-flex items-center gap-1 text-xs font-medium text-cyan-700 hover:text-cyan-900"
          >
            {pending && <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />}
            {pending ? "保存中…" : "保存"}
          </button>
          <button
            type="button"
            onClick={() => {
              setEditing(false);
              setValue(groupType === "new_group" ? "new_group" : "legacy");
            }}
            disabled={pending}
            className="text-xs text-slate-500 hover:text-slate-700"
          >
            取消
          </button>
        </span>
      ) : (
        <span className="inline-flex items-center gap-1.5">
          <span
            className={`status-pill ${
              isNewGroup ? "border-cyan-200 bg-cyan-50 text-cyan-800" : "border-slate-200 bg-slate-100 text-slate-600"
            }`}
          >
            {isNewGroup ? "新群" : "老群"}
          </span>
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="text-xs text-slate-400 hover:text-slate-700"
            title="修改群类型"
          >
            修改
          </button>
        </span>
      )}
    </span>
  );
}
