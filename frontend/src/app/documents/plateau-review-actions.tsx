"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { CheckCheck, LoaderCircle, RotateCcw } from "lucide-react";
import { API_BASE } from "@/lib/api";

/**
 * 审核控制台「体重状态跟进」列表的操作按钮。
 *
 * 只记"运营已经跟进过这一次判定"，不改判定结论本身。跟进之后如果结论又变了
 * （离开平台期又回来、平台期转成反弹），这条会自动重新回到待跟进，红点也跟着回来。
 * 逻辑与 refund-review-actions.tsx 一致。
 */
export function PlateauReviewActions({
  caseId,
  variant = "mark",
}: {
  caseId: number;
  /** mark=标记已跟进（待办列表）；undo=撤销跟进（已跟进列表里点错了能退回来） */
  variant?: "mark" | "undo";
}) {
  const [busy, setBusy] = useState(false);
  const [isPending, startTransition] = useTransition();
  const router = useRouter();
  const pending = busy || isPending;

  useEffect(() => {
    if (!pending) setBusy(false);
  }, [pending]);

  const undo = variant === "undo";

  async function markFollowedUp() {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/service-cases/${caseId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ plateau_followed_up: !undo }),
      });
      if (!res.ok) {
        alert("操作失败：" + (await res.text()));
        return;
      }
      startTransition(() => router.refresh());
    } finally {
      setBusy(false);
    }
  }

  // 待办里这是这一行唯一要做的事，用主按钮；已跟进里的撤销是补救动作，保持安静。
  const Icon = pending ? LoaderCircle : undo ? RotateCcw : CheckCheck;

  return (
    <button
      type="button"
      onClick={markFollowedUp}
      disabled={pending}
      aria-busy={pending || undefined}
      className={`${undo ? "admin-button-secondary" : "btn-primary"} shrink-0 whitespace-nowrap`}
    >
      <Icon className={`h-4 w-4 ${pending ? "animate-spin motion-reduce:animate-none" : ""}`} />
      {pending ? "处理中…" : undo ? "撤销跟进" : "标记已跟进"}
    </button>
  );
}
