"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { API_BASE } from "@/lib/api";

export function DeleteUserButton({ userId, userName }: { userId: number; userName: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function deleteUser() {
    if (!window.confirm(`确认删除用户「${userName}」？删除后历史记录仍可在已删除用户中查看。`)) {
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${API_BASE}/users/${userId}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await response.text());
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      type="button"
      className="h-8 bg-white px-2 text-xs text-red-600 shadow-none ring-1 ring-red-200 hover:bg-red-50"
      disabled={busy}
      onClick={deleteUser}
    >
      {busy ? "删除中" : "删除"}
    </Button>
  );
}
