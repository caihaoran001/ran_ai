"use client";

import Link, { useLinkStatus } from "next/link";
import { ArrowLeft, LoaderCircle } from "lucide-react";

/**
 * 详情页返回键。与大标题同行，视觉上次于标题但有实体感（白底+边框+阴影），
 * 免得像一段可有可无的灰字。挂起状态就地转圈，避免运营重复点。
 */
export function BackToConsoleButton() {
  return (
    <Link
      href="/documents"
      aria-label="返回审核控制台"
      title="返回审核控制台"
      className="group mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 shadow-xs transition-all hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-800 active:scale-95"
    >
      <BackIcon />
    </Link>
  );
}

function BackIcon() {
  const { pending } = useLinkStatus();
  return pending ? (
    <LoaderCircle
      className="h-4 w-4 animate-spin text-cyan-700 motion-reduce:animate-none"
      aria-hidden="true"
    />
  ) : (
    <ArrowLeft className="h-4 w-4 transition-transform group-hover:-translate-x-0.5" aria-hidden="true" />
  );
}
