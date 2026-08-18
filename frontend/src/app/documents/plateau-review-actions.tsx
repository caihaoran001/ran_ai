"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle } from "lucide-react";
import { API_BASE } from "@/lib/api";

/**
 * 审核控制台「体重状态跟进」列表的操作按钮。
 *
 * 只记"运营已经跟进过这一次判定"，不改判定结论本身。跟进之后如果结论又变了
 * （离开平台期又回来、平台期转成反弹），这条会自动重新回到待跟进，红点也跟着回来。
 * 逻辑与 refund-review-actions.tsx 一致。
 */
export function PlateauReviewActions({ caseId }: { caseId: number }) {
  const [busy, setBusy] = useState(false);
  const [isPending, startTransition] = useTransition();
  const router = useRouter();
  const pending = busy || isPending;

  useEffect(() => {
    if (!pending) setBusy(false);
  }, [pending]);

  async function markFollowedUp() {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/service-cases/${caseId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ plateau_followed_up: true }),
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

  return (
    <button
      type="button"
      onClick={markFollowedUp}
      disabled={pending}
      aria-busy={pending || undefined}
      className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
    >
      {pending && <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />}
      {pending ? "处理中…" : "标记已跟进"}
    </button>
  );
}
