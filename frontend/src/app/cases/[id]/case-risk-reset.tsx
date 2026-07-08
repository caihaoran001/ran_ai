"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE } from "@/lib/api";

export function CaseRiskReset({ caseId }: { caseId: number }) {
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const router = useRouter();

  async function resetRisk() {
    if (!confirmReset) {
      setConfirmReset(true);
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/service-cases/${caseId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reset_risk: true }),
      });
      if (res.ok) {
        router.refresh();
      } else {
        alert("操作失败：" + (await res.text()));
      }
    } catch (err) {
      alert("操作出错");
      console.error(err);
    } finally {
      setBusy(false);
      setConfirmReset(false);
    }
  }

  return (
    <div className="inline-flex items-center gap-2">
      {confirmReset && (
        <button
          type="button"
          className="text-xs text-slate-500 hover:text-slate-700"
          onClick={() => setConfirmReset(false)}
          disabled={busy}
        >
          取消
        </button>
      )}
      <button
        onClick={resetRisk}
        disabled={busy}
        className={`ml-2 inline-flex items-center text-xs font-medium transition-all ${
          confirmReset
            ? "text-orange-600 hover:text-orange-700 font-semibold underline"
            : "text-slate-500 hover:text-slate-900"
        }`}
      >
        {busy ? "处理中..." : confirmReset ? "确认重置？" : "重置风险等级"}
      </button>
    </div>
  );
}
