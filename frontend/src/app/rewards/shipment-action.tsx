"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, PackageCheck } from "lucide-react";
import { API_BASE } from "@/lib/api";

export function ShipmentAction({ shipmentId }: { shipmentId: number }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [isPending, startTransition] = useTransition();
  const router = useRouter();
  const pending = busy || isPending;

  async function markShipped() {
    setBusy(true);
    try {
      const response = await fetch(
        `${API_BASE}/checkin-rewards/shipments/${shipmentId}/mark-shipped`,
        { method: "POST" },
      );
      if (!response.ok) {
        window.alert(`操作失败：${await response.text()}`);
        return;
      }
      startTransition(() => router.refresh());
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {confirming && !pending ? (
        <button
          type="button"
          className="text-xs font-semibold text-slate-500 hover:text-slate-800"
          onClick={() => setConfirming(false)}
        >
          取消
        </button>
      ) : null}
      <button
        type="button"
        disabled={pending}
        aria-busy={pending || undefined}
        onClick={() => (confirming ? void markShipped() : setConfirming(true))}
        className={`btn-primary ${
          confirming ? "bg-emerald-700 hover:bg-emerald-800" : ""
        }`}
      >
        {pending ? (
          <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" />
        ) : (
          <PackageCheck className="h-4 w-4" />
        )}
        {pending ? "正在登记…" : confirming ? "确认已经实际寄出" : "标记已发货"}
      </button>
    </div>
  );
}
