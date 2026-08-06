"use client";

import Link, { useLinkStatus } from "next/link";
import { ArrowLeft, LoaderCircle } from "lucide-react";

/**
 * 详情页返回上一层的图标键。与大标题同行，白底+边框+阴影，视觉次于标题但有实体感。
 * 挂起时就地转圈，避免运营重复点。
 *
 * `scroll={false}`：不让路由把列表页滚到顶部——列表页自己按进入前记录的位置还原，
 * 否则运营每次返回都被丢回页首、还得重新往下滚。
 */
export function BackIconLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      scroll={false}
      aria-label={label}
      title={label}
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
    <ArrowLeft
      className="h-4 w-4 transition-transform group-hover:-translate-x-0.5"
      aria-hidden="true"
    />
  );
}
