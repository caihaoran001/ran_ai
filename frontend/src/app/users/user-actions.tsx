"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { API_BASE } from "@/lib/api";

export function DeleteServiceCaseButton({
  serviceCaseId,
  userName,
}: {
  serviceCaseId: number;
  userName: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function deleteServiceCase() {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${API_BASE}/service-cases/${serviceCaseId}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await response.text());
      router.refresh();
    } catch (err: any) {
      alert(`删除失败: ${err.message || err}`);
    } finally {
      setBusy(false);
      setConfirmDelete(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {confirmDelete && (
        <button
          type="button"
          className="text-xs text-slate-500 hover:text-slate-700"
          onClick={() => setConfirmDelete(false)}
          disabled={busy}
        >
          取消
        </button>
      )}
      <Button
        type="button"
        className={`h-8 px-2 text-xs shadow-none ring-1 transition-all ${
          confirmDelete
            ? "bg-red-600 text-white hover:bg-red-700 ring-red-600 font-semibold"
            : "bg-white text-red-600 ring-red-200 hover:bg-red-50"
        }`}
        loading={busy}
        loadingText="删除中…"
        onClick={deleteServiceCase}
      >
        {confirmDelete ? "确定删除？" : "删除"}
      </Button>
    </div>
  );
}
