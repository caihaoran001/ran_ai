"use client";

import Link from "next/link";
import { LoaderCircle } from "lucide-react";
import { flushSync } from "react-dom";
import { useEffect, useState, useTransition, type MouseEvent } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

export type UserFilterView = "normal" | "active" | "deleted";

const FILTER_TABS: Array<{ view: UserFilterView; href: string; label: string }> = [
  { view: "normal", href: "/users", label: "正常用户" },
  { view: "active", href: "/users?active=1", label: "活跃用户" },
  { view: "deleted", href: "/users?deleted=1", label: "已删除" },
];

function normalizeHref(href: string) {
  const url = new URL(href, "http://internal");
  url.searchParams.sort();
  const query = url.searchParams.toString();
  return `${url.pathname}${query ? `?${query}` : ""}`;
}

export function UserFilterTabs({ currentView }: { currentView: UserFilterView }) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [pendingHref, setPendingHref] = useState<string | null>(null);
  const [, startNavigation] = useTransition();
  const currentParams = new URLSearchParams(searchParams.toString());
  currentParams.sort();
  const currentQuery = currentParams.toString();
  const currentHref = `${pathname}${currentQuery ? `?${currentQuery}` : ""}`;

  useEffect(() => {
    if (pendingHref && normalizeHref(pendingHref) === currentHref) {
      setPendingHref(null);
    }
  }, [currentHref, pendingHref]);

  function navigate(
    event: MouseEvent<HTMLAnchorElement>,
    href: string,
  ) {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }

    if (pendingHref || normalizeHref(href) === currentHref) {
      event.preventDefault();
      return;
    }

    event.preventDefault();
    flushSync(() => setPendingHref(href));
    startNavigation(() => router.push(href));
  }

  return (
    <nav
      aria-label="用户范围筛选"
      aria-busy={Boolean(pendingHref)}
      className="mt-4 grid w-full grid-cols-3 rounded-lg border border-slate-200 bg-slate-100 p-1 text-sm shadow-inner sm:w-fit"
    >
      {FILTER_TABS.map((tab) => {
        const active = currentView === tab.view;
        const pending = pendingHref === tab.href;
        return (
          <Link
            key={tab.view}
            href={tab.href}
            prefetch={false}
            onClick={(event) => navigate(event, tab.href)}
            aria-current={active ? "page" : undefined}
            aria-busy={pending || undefined}
            aria-disabled={Boolean(pendingHref) || undefined}
            className={`flex min-h-10 min-w-0 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-center font-semibold transition-colors duration-200 ${
              pending
                ? "bg-cyan-50 text-cyan-900 ring-1 ring-inset ring-cyan-200"
                : active
                  ? "bg-white text-cyan-800 shadow-sm"
                  : "text-slate-700 hover:bg-white/70 hover:text-slate-950"
            } ${pendingHref ? "cursor-wait" : ""}`}
          >
            {pending ? (
              <>
                <LoaderCircle
                  className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none"
                  aria-hidden="true"
                />
                <span>加载中…</span>
                <span className="sr-only">正在切换到{tab.label}</span>
              </>
            ) : (
              tab.label
            )}
          </Link>
        );
      })}
    </nav>
  );
}
