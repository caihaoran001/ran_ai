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
    <div className="min-h-screen bg-slate-50">
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white lg:hidden">
        <div className="flex h-14 items-center justify-between px-4">
          <Link href="/dashboard" className="flex min-w-0 items-center gap-2 font-bold text-slate-950">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyan-700 text-sm font-black text-white">
              R
            </span>
            <span className="truncate">燃人AI 1.0</span>
          </Link>
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
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
                className={`relative inline-flex min-h-10 shrink-0 items-center gap-2 rounded-lg border px-3 text-sm font-semibold transition-colors ${
                  isActive
                    ? "border-cyan-700 bg-cyan-700 text-white"
                    : "border-slate-200 bg-white text-slate-700 hover:border-cyan-200 hover:bg-cyan-50 hover:text-cyan-800"
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

      <aside className="fixed inset-y-0 left-0 z-50 hidden w-64 border-r border-slate-200 bg-white shadow-sm lg:block">
        <div className="flex h-16 items-center border-b border-slate-100 px-6">
          <Link href="/dashboard" className="flex items-center gap-3 text-xl font-bold text-slate-950">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-cyan-700 text-sm font-black text-white shadow-sm">
              R
            </span>
            <span>
              燃人AI <span className="text-cyan-700">1.0</span>
            </span>
          </Link>
        </div>
        <nav className="space-y-1 p-4">
          {navItems.map((item) => {
            const isActive = pathname === item.href || pathname?.startsWith(`${item.href}/`);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={`group flex min-h-11 items-center gap-3 rounded-lg px-4 py-2.5 text-sm font-semibold transition-colors duration-200 ${
                  isActive
                    ? "bg-cyan-700 text-white"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
                }`}
              >
                <Icon
                  className={`h-[18px] w-[18px] transition-colors duration-200 ${
                    isActive 
                      ? "text-white"
                      : "text-slate-400 group-hover:text-slate-700"
                  }`}
                />
                <span className="relative flex items-center">
                  {item.label}
                  {item.href === "/documents" && <InterventionBadge />}
                </span>
              </Link>
            );
          })}
        </nav>
        
        <div className="absolute bottom-0 left-0 right-0 border-t border-slate-100 bg-white p-4">
          <div className="flex items-center gap-2 text-xs font-semibold text-slate-400">
            <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            燃人AI 内部运营系统 v1.0.0
          </div>
        </div>
      </aside>

      <main className="min-w-0 lg:pl-64">
        <div className="mx-auto max-w-[1500px] px-4 py-5 sm:px-6 sm:py-6 lg:px-8 lg:py-8">
          {children}
        </div>
      </main>
    </div>
  );
}
