"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle } from "lucide-react";
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
 * 审核控制台「退款审核」列表的紧凑操作按钮：
 * - 确认退款 → status=REFUNDED，打卡/阶段报告等产品类推送自动停止，AI 群聊照常。
 * - 不是退款 → 仅清除候选标记，服务照常。
 * 逻辑镜像 cases/[id]/case-refund-controls.tsx。
 */
export function RefundReviewActions({ caseId }: { caseId: number }) {
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<"confirm" | "dismiss" | null>(null);
  const [isPending, startTransition] = useTransition();
  const router = useRouter();
  // 保持禁用直到整页刷新完成，避免运营误以为卡住而重复点击。
  const pending = busy || isPending;

  useEffect(() => {
    if (!pending) setPendingAction(null);
  }, [pending]);

  async function run(action: NonNullable<typeof pendingAction>, body: Record<string, unknown>) {
    setPendingAction(action);
    setBusy(true);
    try {
      if (await patchCase(caseId, body)) {
        startTransition(() => router.refresh());
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      <button
        type="button"
        onClick={() => run("confirm", { status: "refunded", refund_pending_confirmation: false })}
        disabled={pending}
        aria-busy={pending && pendingAction === "confirm" || undefined}
        className="inline-flex items-center gap-1.5 rounded-md bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-rose-700 disabled:opacity-50"
      >
        {pending && pendingAction === "confirm" && <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />}
        {pending && pendingAction === "confirm" ? "处理中…" : "确认退款"}
      </button>
      <button
        type="button"
        onClick={() => run("dismiss", { refund_pending_confirmation: false })}
        disabled={pending}
        aria-busy={pending && pendingAction === "dismiss" || undefined}
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
      >
        {pending && pendingAction === "dismiss" && <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />}
        {pending && pendingAction === "dismiss" ? "处理中…" : "不是退款"}
      </button>
    </div>
  );
}
