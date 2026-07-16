"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { RotateCw } from "lucide-react";

export function RetryButton() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  return (
    <button
      type="button"
      className="btn-primary"
      disabled={pending}
      onClick={() => startTransition(() => router.refresh())}
    >
      <RotateCw className={`h-4 w-4 ${pending ? "animate-spin" : ""}`} aria-hidden="true" />
      {pending ? "正在重新加载" : "重新加载"}
    </button>
  );
}
