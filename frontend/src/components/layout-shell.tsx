"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { Activity, BarChart3, Settings, ShieldCheck, Users } from "lucide-react";
import { InterventionBadge } from "@/components/intervention-notifier";

const navItems = [
  { href: "/users", label: "用户管理", icon: Users },
  { href: "/documents", label: "审核控制台", icon: ShieldCheck },
  { href: "/questionnaire-config", label: "运营规则配置", icon: Settings },
  { href: "/dashboard", label: "数据看板", icon: BarChart3 },
  { href: "/system-diagnostics", label: "诊断控制台", icon: Activity },
];

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isNoShell = pathname?.startsWith("/q/") || pathname === "/login";

  if (isNoShell) {
    return (
      <div className="min-h-screen bg-slate-50">
        <main>
          {children}
        </main>
      </div>
    );
  }

  return (
    <div className="admin-shell">
      <header className="sticky top-0 z-40 border-b border-slate-800 bg-slate-950 text-white shadow-sm lg:hidden">
        <div className="flex h-14 items-center justify-between px-4">
          <Link href="/dashboard" className="flex min-w-0 items-center gap-2 font-bold text-white">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyan-500 text-sm font-black text-cyan-950">
              R
            </span>
            <span className="truncate">燃人AI 1.0</span>
          </Link>
          <span className="rounded-full border border-emerald-400/40 bg-emerald-400/10 px-2.5 py-1 text-xs font-bold text-emerald-200">
            内部运营
          </span>
        </div>
        <nav className="flex gap-2 overflow-x-auto px-3 pb-3">
          {navItems.map((item) => {
            const isActive = pathname === item.href || pathname?.startsWith(`${item.href}/`);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={`relative inline-flex min-h-10 shrink-0 items-center gap-2 rounded-lg border px-3 text-sm font-bold transition-colors ${
                  isActive
                    ? "border-cyan-400 bg-cyan-400 text-cyan-950"
                    : "border-slate-700 bg-slate-900 text-slate-200 hover:border-cyan-500 hover:bg-slate-800 hover:text-white"
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

      <aside className="fixed inset-y-0 left-0 z-50 hidden w-[17rem] border-r border-slate-800 bg-slate-950 text-white shadow-sm lg:block">
        <div className="border-b border-slate-800 px-5 py-5">
          <Link href="/dashboard" className="flex items-center gap-3 text-white">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-cyan-400 text-sm font-black text-cyan-950">
              R
            </span>
            <span className="min-w-0">
              <span className="block text-lg font-black leading-5">燃人AI 1.0</span>
              <span className="mt-1 block text-xs font-semibold text-slate-400">内部社群运营系统</span>
            </span>
          </Link>
        </div>
        <nav className="space-y-1.5 p-3">
          {navItems.map((item) => {
            const isActive = pathname === item.href || pathname?.startsWith(`${item.href}/`);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={`group flex min-h-11 items-center gap-3 rounded-lg px-3.5 py-2.5 text-sm font-bold transition-colors duration-200 ${
                  isActive
                    ? "bg-cyan-400 text-cyan-950"
                    : "text-slate-300 hover:bg-slate-900 hover:text-white"
                }`}
              >
                <Icon
                  className={`h-[18px] w-[18px] transition-colors duration-200 ${
                    isActive 
                      ? "text-slate-950"
                      : "text-slate-500 group-hover:text-cyan-300"
                  }`}
                />
                <span className="relative flex min-w-0 items-center">
                  {item.label}
                  {item.href === "/documents" && <InterventionBadge />}
                </span>
              </Link>
            );
          })}
        </nav>
        
        <div className="absolute bottom-0 left-0 right-0 border-t border-slate-800 bg-slate-950 p-4">
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-3">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-100">
              <div className="h-2 w-2 rounded-full bg-emerald-500" />
              系统运行中
            </div>
            <div className="mt-1 text-xs leading-5 text-slate-400">
              v1.0.0 · 管理员后台
            </div>
          </div>
        </div>
      </aside>

      <main className="min-w-0 lg:pl-[17rem]">
        <div className="mx-auto max-w-[1500px] px-4 py-5 sm:px-6 sm:py-6 lg:px-8 lg:py-8">
          {children}
        </div>
      </main>
    </div>
  );
}
