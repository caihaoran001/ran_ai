"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { Activity, BarChart3, LogOut, PieChart, Settings, ShieldCheck, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { InterventionBadge } from "@/components/intervention-notifier";

type NavItem = { href: string; label: string; icon: LucideIcon };
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
    title: "监控",
    items: [
      { href: "/dashboard", label: "数据看板", icon: BarChart3 },
      { href: "/agent-operations", label: "AI 运行", icon: Activity },
      { href: "/user-segments", label: "群体画像", icon: PieChart },
    ],
  },
];

const flatNav = navGroups.flatMap((g) => g.items);

// Labels for top-level routes reachable outside the sidebar (breadcrumb only).
const extraRouteLabels: Record<string, string> = {
  "/cases": "服务单",
  "/checkins": "打卡记录",
};

function isActivePath(pathname: string | null, href: string) {
  // href 可能带 query(如 /users?active=1)，高亮只按路径部分匹配。
  const base = href.split("?")[0];
  return pathname === base || pathname?.startsWith(`${base}/`) || false;
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/dashboard" className="group flex min-w-0 items-center gap-3 text-white">
      <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-400 to-cyan-600 text-sm font-black text-cyan-950 shadow-lg shadow-cyan-500/20 ring-1 ring-inset ring-white/20">
        R
        <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-slate-950 bg-emerald-400" />
      </span>
      {!compact && (
        <span className="min-w-0">
          <span className="block truncate text-[0.95rem] font-extrabold leading-5 tracking-tight">燃人AI 2.0</span>
          <span className="mt-0.5 block truncate text-[0.7rem] font-medium text-slate-400">内部社群运营系统</span>
        </span>
      )}
    </Link>
  );
}

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isNoShell = pathname?.startsWith("/q/") || pathname === "/login";

  if (isNoShell) {
    return (
      <div className="min-h-screen bg-slate-50">
        <main>{children}</main>
      </div>
    );
  }

  const currentLabel =
    flatNav.find((item) => isActivePath(pathname, item.href))?.label ??
    Object.entries(extraRouteLabels).find(([href]) => isActivePath(pathname, href))?.[1] ??
    null;

  return (
    <div className="admin-shell">
      {/* ------------------------------------------------ Mobile top nav */}
      <header
        className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/95 text-white backdrop-blur lg:hidden"
        style={{ zIndex: "var(--z-sticky)" }}
      >
        <div className="flex h-14 items-center justify-between px-4">
          <Brand compact />
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1 text-xs font-semibold text-emerald-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            内部运营
          </span>
        </div>
        <nav className="flex gap-2 overflow-x-auto px-3 pb-3 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {flatNav.map((item) => {
            const active = isActivePath(pathname, item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`relative inline-flex min-h-10 shrink-0 items-center gap-2 rounded-xl border px-3 text-sm font-semibold transition-colors ${
                  active
                    ? "border-cyan-400/60 bg-cyan-400/15 text-cyan-200"
                    : "border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700 hover:text-white"
                }`}
              >
                <Icon className="h-4 w-4" />
                <span>{item.label}</span>
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
          <Brand />
        </div>

        <nav className="relative flex-1 space-y-6 overflow-y-auto px-3 py-5">
          {navGroups.map((group) => (
            <div key={group.title}>
              <div className="px-3 pb-2 text-[0.68rem] font-bold uppercase tracking-widest text-slate-500">
                {group.title}
              </div>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const active = isActivePath(pathname, item.href);
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={`group relative flex min-h-11 items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-semibold transition-all duration-200 ${
                        active
                          ? "bg-white/[0.06] text-white ring-1 ring-inset ring-white/10"
                          : "text-slate-400 hover:bg-white/[0.04] hover:text-white"
                      }`}
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
                        <Icon className="h-[18px] w-[18px]" />
                      </span>
                      <span className="relative flex min-w-0 items-center">
                        {item.label}
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
              {currentLabel ? (
                <>
                  <span className="font-medium text-slate-400">工作台</span>
                  <span className="text-slate-300">/</span>
                  <span className="truncate font-bold text-slate-900">{currentLabel}</span>
                </>
              ) : (
                <span className="truncate font-bold text-slate-900">工作台</span>
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
                  onClick={() => {
                    if (window.confirm("确定要退出登录吗？")) {
                      window.location.href = "/api/auth/logout";
                    }
                  }}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-semibold text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900"
                  title="退出登录"
                >
                  <LogOut className="h-4 w-4" />
                  <span className="hidden xl:inline">退出</span>
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
