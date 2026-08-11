"use client";

import { flushSync } from "react-dom";
import { useEffect, useRef, useState, useTransition, type MouseEvent } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { Activity, BarChart3, LoaderCircle, LogOut, PieChart, Settings, ShieldCheck, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { InterventionBadge } from "@/components/intervention-notifier";

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  activeHrefs?: string[];
};
type NavGroup = { title: string; items: NavItem[] };

const navGroups: NavGroup[] = [
  {
    title: "运营",
    items: [
      { href: "/users?active=1", label: "用户管理", icon: Users },
      { href: "/documents", label: "审核控制台", icon: ShieldCheck },
      { href: "/questionnaire-config", label: "运营规则配置", icon: Settings },
    ],
  },
  {
    title: "数据",
    items: [
      { href: "/dashboard", label: "数据看板", icon: BarChart3 },
      { href: "/user-segments", label: "群体画像", icon: PieChart },
    ],
  },
  {
    title: "系统",
    items: [
      {
        href: "/agent-operations",
        label: "AI 管理",
        icon: Activity,
        activeHrefs: ["/rag-evaluations"],
      },
    ],
  },
];

const flatNav = navGroups.flatMap((g) => g.items);

// Labels for top-level routes reachable outside the sidebar (breadcrumb only).
const extraRouteLabels: Record<string, string> = {
  "/cases": "服务单",
  "/checkins": "打卡记录",
};

// 二级详情页的名字。没有这层，面包屑会把"文件详情"标成"审核控制台"，
// 既说不清当前在哪，也没有可点回上一层的入口。
const detailRouteLabels: Array<{ pattern: RegExp; label: string }> = [
  { pattern: /^\/documents\/[^/]+$/, label: "交付文件审核" },
  { pattern: /^\/cases\/[^/]+$/, label: "服务单详情" },
  { pattern: /^\/users\/[^/]+$/, label: "用户详情" },
];

function isActivePath(pathname: string | null, href: string) {
  // href 可能带 query(如 /users?active=1)，高亮只按路径部分匹配。
  const base = href.split("?")[0];
  return pathname === base || pathname?.startsWith(`${base}/`) || false;
}

function isActiveNavItem(pathname: string | null, item: NavItem) {
  return [item.href, ...(item.activeHrefs ?? [])].some((href) =>
    isActivePath(pathname, href),
  );
}

function Brand({
  compact = false,
  pending = false,
  onClick,
}: {
  compact?: boolean;
  pending?: boolean;
  onClick: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  return (
    <Link
      href="/dashboard"
      onClick={onClick}
      aria-busy={pending || undefined}
      className="group flex min-w-0 items-center gap-3 text-white"
    >
      <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-400 to-cyan-600 text-sm font-black text-cyan-950 shadow-lg shadow-cyan-500/20 ring-1 ring-inset ring-white/20">
        R
        {pending ? (
          <span className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full border-2 border-slate-950 bg-cyan-300 text-slate-950">
            <LoaderCircle className="h-2.5 w-2.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          </span>
        ) : (
          <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-slate-950 bg-emerald-400" />
        )}
      </span>
      {!compact && (
        <span className="min-w-0">
          <span className="block truncate text-[0.95rem] font-extrabold leading-5 tracking-tight">燃人AI 2.0</span>
          <span className="mt-0.5 block truncate text-[0.7rem] font-medium text-slate-400">
            {pending ? "正在进入数据看板…" : "内部社群运营系统"}
          </span>
        </span>
      )}
    </Link>
  );
}

function BreadcrumbLink({
  href,
  label,
  pending,
  disabled,
  onNavigate,
}: {
  href: string;
  label: string;
  pending: boolean;
  disabled: boolean;
  onNavigate: (event: MouseEvent<HTMLAnchorElement>, href: string) => void;
}) {
  return (
    <Link
      href={href}
      onClick={(event) => onNavigate(event, href)}
      aria-busy={pending || undefined}
      aria-disabled={disabled || undefined}
      className={`inline-flex shrink-0 items-center gap-1 rounded font-medium text-slate-500 transition-colors hover:text-cyan-800 hover:underline ${
        disabled && !pending ? "opacity-55" : ""
      }`}
    >
      {pending && (
        <LoaderCircle className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      )}
      <span>{label}</span>
    </Link>
  );
}

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [pendingHref, setPendingHref] = useState<string | null>(null);
  const [, startNavigation] = useTransition();
  const [loggingOut, setLoggingOut] = useState(false);
  const mobileNavRef = useRef<HTMLElement>(null);
  const isNoShell = pathname?.startsWith("/q/") || pathname === "/login";

  useEffect(() => {
    if (!pendingHref) return;
    const target = new URL(pendingHref, window.location.origin);
    const clearWhenCommitted = () => {
      if (
        target.pathname === window.location.pathname &&
        target.search === window.location.search
      ) {
        setPendingHref(null);
      }
    };
    clearWhenCommitted();
    const timer = window.setInterval(clearWhenCommitted, 50);
    return () => window.clearInterval(timer);
  }, [pathname, pendingHref]);

  useEffect(() => {
    const activeItem = mobileNavRef.current?.querySelector<HTMLElement>(
      'a[aria-current="page"]',
    );
    activeItem?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [pathname]);

  function navigate(event: MouseEvent<HTMLAnchorElement>, href: string) {
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

    const target = new URL(href, window.location.href);
    const current = `${window.location.pathname}${window.location.search}`;
    const destination = `${target.pathname}${target.search}`;

    if (destination === current || pendingHref) {
      event.preventDefault();
      return;
    }

    event.preventDefault();
    // 先同步显示点击反馈，再把服务端路由加载放进 transition。
    flushSync(() => setPendingHref(href));
    startNavigation(() => router.push(href));
  }

  function logout() {
    if (loggingOut || !window.confirm("确定要退出登录吗？")) return;
    flushSync(() => setLoggingOut(true));
    window.location.assign("/api/auth/logout");
  }

  if (isNoShell) {
    return (
      <div className="min-h-screen bg-slate-50">
        <main>{children}</main>
      </div>
    );
  }

  const sectionMatch =
    flatNav.find((item) => isActiveNavItem(pathname, item)) ??
    Object.entries(extraRouteLabels)
      .map(([href, label]) => ({ href, label }))
      .find((item) => isActivePath(pathname, item.href)) ??
    null;
  const sectionLabel = sectionMatch?.label ?? null;
  const detailLabel = pathname
    ? detailRouteLabels.find((item) => item.pattern.test(pathname))?.label ?? null
    : null;

  return (
    <div className="admin-shell">
      {/* ------------------------------------------------ Mobile top nav */}
      <header
        className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/95 text-white backdrop-blur lg:hidden"
        style={{ zIndex: "var(--z-sticky)" }}
      >
        <div className="flex h-14 items-center justify-between px-4">
          <Brand
            compact
            pending={pendingHref === "/dashboard"}
            onClick={(event) => navigate(event, "/dashboard")}
          />
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1 text-xs font-semibold text-emerald-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            内部运营
          </span>
        </div>
        <nav
          ref={mobileNavRef}
          className="flex gap-2 overflow-x-auto px-3 pb-3 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {flatNav.map((item) => {
            const active = isActiveNavItem(pathname, item);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={(event) => navigate(event, item.href)}
                aria-current={active ? "page" : undefined}
                aria-busy={pendingHref === item.href || undefined}
                aria-disabled={Boolean(pendingHref) || undefined}
                className={`relative inline-flex min-h-10 shrink-0 items-center gap-2 rounded-xl border px-3 text-sm font-semibold transition-colors ${
                  active
                    ? "border-cyan-400/60 bg-cyan-400/15 text-cyan-200"
                    : "border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700 hover:text-white"
                } ${pendingHref && pendingHref !== item.href ? "opacity-55" : ""}`}
              >
                {pendingHref === item.href ? (
                  <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                ) : (
                  <Icon className="h-4 w-4" />
                )}
                <span>{pendingHref === item.href ? "进入中…" : item.label}</span>
                {item.href === "/documents" && <InterventionBadge />}
              </Link>
            );
          })}
        </nav>
      </header>

      {/* --------------------------------------------------- Desktop sidebar */}
      <aside
        className="fixed inset-y-0 left-0 hidden w-[17rem] flex-col border-r border-slate-800/80 bg-slate-950 text-white lg:flex"
        style={{ zIndex: "var(--z-sidebar)" }}
      >
        {/* subtle depth gradient */}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-slate-900/60 via-slate-950 to-slate-950" />
        <div className="relative flex items-center border-b border-slate-800/80 px-5 py-5">
          <Brand
            pending={pendingHref === "/dashboard"}
            onClick={(event) => navigate(event, "/dashboard")}
          />
        </div>

        <nav className="relative flex-1 space-y-6 overflow-y-auto px-3 py-5">
          {navGroups.map((group) => (
            <div key={group.title}>
              <div className="px-3 pb-2 text-[0.68rem] font-bold uppercase tracking-widest text-slate-500">
                {group.title}
              </div>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const active = isActiveNavItem(pathname, item);
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={(event) => navigate(event, item.href)}
                      aria-current={active ? "page" : undefined}
                      aria-busy={pendingHref === item.href || undefined}
                      aria-disabled={Boolean(pendingHref) || undefined}
                      className={`group relative flex min-h-11 items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-semibold transition-all duration-200 ${
                        active
                          ? "bg-white/[0.06] text-white ring-1 ring-inset ring-white/10"
                          : "text-slate-400 hover:bg-white/[0.04] hover:text-white"
                      } ${pendingHref && pendingHref !== item.href ? "opacity-55" : ""}`}
                    >
                      {/* left active indicator (rounded bar, not a border stripe) */}
                      <span
                        className={`absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-cyan-400 transition-all duration-200 ${
                          active ? "opacity-100" : "opacity-0"
                        }`}
                      />
                      <span
                        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-colors ${
                          active ? "bg-cyan-400/15 text-cyan-300" : "text-slate-500 group-hover:text-cyan-300"
                        }`}
                      >
                        {pendingHref === item.href ? (
                          <LoaderCircle
                            className="h-[18px] w-[18px] animate-spin text-cyan-300 motion-reduce:animate-none"
                            aria-hidden="true"
                          />
                        ) : (
                          <Icon className="h-[18px] w-[18px]" />
                        )}
                      </span>
                      <span className="relative flex min-w-0 items-center">
                        {pendingHref === item.href ? "正在进入…" : item.label}
                        {item.href === "/documents" && <InterventionBadge />}
                      </span>
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="relative border-t border-slate-800/80 p-3">
          <div className="rounded-xl border border-slate-800/80 bg-slate-900/60 p-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-200">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/70" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
              </span>
              系统运行中
            </div>
            <div className="mt-1 text-[0.7rem] leading-5 text-slate-400">v2.0.0 · 管理员后台</div>
          </div>
        </div>
      </aside>

      {/* ------------------------------------------------------------ Main */}
      <div className="min-w-0 lg:pl-[17rem]">
        {/* Desktop workspace top bar */}
        <header
          className="sticky top-0 hidden border-b border-slate-200/70 bg-white/80 backdrop-blur-md lg:block"
          style={{ zIndex: "var(--z-sticky)" }}
        >
          <div className="mx-auto flex max-w-[1500px] items-center justify-between gap-4 px-8 py-3">
            <nav aria-label="面包屑" className="flex min-w-0 items-center gap-2 text-sm">
              {sectionLabel ? (
                <>
                  <BreadcrumbLink
                    href="/dashboard"
                    label="工作台"
                    pending={pendingHref === "/dashboard"}
                    disabled={Boolean(pendingHref)}
                    onNavigate={navigate}
                  />
                  <span className="text-slate-300">/</span>
                  {detailLabel && sectionMatch ? (
                    <>
                      <BreadcrumbLink
                        href={sectionMatch.href}
                        label={sectionLabel}
                        pending={pendingHref === sectionMatch.href}
                        disabled={Boolean(pendingHref)}
                        onNavigate={navigate}
                      />
                      <span className="text-slate-300">/</span>
                      <span aria-current="page" className="truncate font-bold text-slate-900">
                        {detailLabel}
                      </span>
                    </>
                  ) : (
                    <span aria-current="page" className="truncate font-bold text-slate-900">
                      {sectionLabel}
                    </span>
                  )}
                </>
              ) : (
                <span aria-current="page" className="truncate font-bold text-slate-900">
                  工作台
                </span>
              )}
            </nav>
            <div className="flex shrink-0 items-center gap-3">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                内部运营环境
              </span>
              <div className="h-5 w-px bg-slate-200" />
              <div className="flex items-center gap-2">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-900 text-xs font-bold text-white">
                  管
                </span>
                <button
                  type="button"
                  onClick={logout}
                  disabled={loggingOut}
                  aria-busy={loggingOut || undefined}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-semibold text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900"
                  title="退出登录"
                >
                  {loggingOut ? (
                    <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                  ) : (
                    <LogOut className="h-4 w-4" />
                  )}
                  <span className="hidden xl:inline">{loggingOut ? "退出中…" : "退出"}</span>
                </button>
              </div>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1500px] px-4 py-5 sm:px-6 sm:py-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
