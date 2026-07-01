"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, RefreshCcw, RotateCcw } from "lucide-react";
import { API_BASE } from "@/lib/api";

async function patchCase(caseId: number, body: Record<string, unknown>): Promise<boolean> {
  const res = await fetch(`${API_BASE}/service-cases/${caseId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    alert("操作失败：" + (await res.text()));
    return false;
  }
  return true;
}

/**
 * 退款闭环操作：
 * - 候选态(refundPending)：模型识别到疑似已退款，显眼提示 +「确认退款」「不是退款」。
 * - 已退款(refunded)：提供「恢复服务」回到服务中。
 * - 常态：提供「标记已退款」兜底(群外退款模型识别不到)。
 * 确认退款后 status=REFUNDED，打卡/阶段报告等产品类主动推送自动停止，AI 群聊照常。
 */
export function CaseRefundControls({
  caseId,
  status,
  refundPending,
}: {
  caseId: number;
  status: string;
  refundPending: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [confirmMark, setConfirmMark] = useState(false);
  const router = useRouter();
  const refunded = status === "refunded";

  async function run(body: Record<string, unknown>) {
    setBusy(true);
    try {
      if (await patchCase(caseId, body)) router.refresh();
    } finally {
      setBusy(false);
      setConfirmMark(false);
    }
  }

  // 候选态：优先展示确认横幅（即使已是 refunded 也不会进这里，因为确认后会清掉候选）
  if (refundPending && !refunded) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4">
        <p className="flex items-center gap-2 text-sm font-semibold text-rose-700">
          <AlertTriangle className="h-4 w-4" />
          疑似已退款，待确认
        </p>
        <p className="mt-1 text-xs leading-5 text-rose-700/80">
          AI 从群聊中识别到该客户可能已完成退款。请核实后确认——确认后将停止打卡提醒、阶段报告等产品类主动推送，群内 AI 聊天仍保留。
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => run({ status: "refunded", refund_pending_confirmation: false })}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-md bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-rose-700 disabled:opacity-50"
          >
            确认退款
          </button>
          <button
            type="button"
            onClick={() => run({ refund_pending_confirmation: false })}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
          >
            不是退款
          </button>
        </div>
      </div>
    );
  }

  // 已退款态：恢复服务
  if (refunded) {
    return (
      <button
        type="button"
        onClick={() => run({ status: "in_service", refund_pending_confirmation: false })}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-emerald-300 hover:bg-emerald-50 hover:text-emerald-800 disabled:opacity-50"
      >
        <RotateCcw className="h-3.5 w-3.5" />
        {busy ? "处理中…" : "恢复服务"}
      </button>
    );
  }

  // 常态：手动标记已退款（兜底）
  return (
    <div className="inline-flex items-center gap-2">
      {confirmMark && (
        <button
          type="button"
          className="text-xs text-slate-500 hover:text-slate-700"
          onClick={() => setConfirmMark(false)}
          disabled={busy}
        >
          取消
        </button>
      )}
      <button
        type="button"
        onClick={() => (confirmMark ? run({ status: "refunded", refund_pending_confirmation: false }) : setConfirmMark(true))}
        disabled={busy}
        className={`inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-semibold transition-all disabled:opacity-50 ${
          confirmMark
            ? "border-rose-600 bg-rose-600 text-white hover:bg-rose-700"
            : "border-rose-200 bg-rose-50 text-rose-600 hover:border-rose-300 hover:bg-rose-100"
        }`}
      >
        <RefreshCcw className="h-3.5 w-3.5" />
        {busy ? "处理中…" : confirmMark ? "确认标记已退款？" : "标记已退款"}
      </button>
    </div>
  );
}
