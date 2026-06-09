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
      <aside className="fixed inset-y-0 left-0 z-50 w-64 border-r border-slate-200 bg-white shadow-sm">
        <div className="flex h-16 items-center border-b border-slate-100 px-6">
          <div className="flex items-center gap-3 text-xl font-bold text-slate-950">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-cyan-700 text-sm font-black text-white shadow-sm">
              R
            </span>
            <span>
              燃AI <span className="text-cyan-700">1.0</span>
            </span>
          </div>
        </div>
        <nav className="space-y-1 p-4">
          {navItems.map((item) => {
            const isActive = pathname === item.href || pathname?.startsWith(`${item.href}/`);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`group relative flex min-h-11 items-center gap-3 rounded-lg px-4 py-2.5 text-sm font-semibold transition-colors duration-200 ${
                  isActive
                    ? "bg-cyan-50 text-cyan-800"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
                }`}
              >
                {isActive && (
                  <div className="absolute left-0 top-2 h-7 w-1 rounded-r-md bg-cyan-700" />
                )}
                <Icon
                  className={`h-[18px] w-[18px] transition-colors duration-200 ${
                    isActive 
                      ? "text-cyan-700"
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
            燃AI 内部运营系统 v1.0.0
          </div>
        </div>
      </aside>

      <main className="pl-64">
        <div className="mx-auto max-w-[1500px] px-8 py-8">
          {children}
        </div>
      </main>
    </div>
  );
}
