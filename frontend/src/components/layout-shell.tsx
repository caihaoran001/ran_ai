"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { Users, ShieldCheck, Settings, BarChart3 } from "lucide-react";
import { InterventionBadge } from "@/components/intervention-notifier";

const navItems = [
  { href: "/users", label: "用户管理", icon: Users },
  { href: "/documents", label: "审核控制台", icon: ShieldCheck },
  { href: "/questionnaire-config", label: "运营规则配置", icon: Settings },
  { href: "/dashboard", label: "数据看板", icon: BarChart3 },
];

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isQuestionnaire = pathname?.startsWith("/q/");

  if (isQuestionnaire) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-zinc-50 to-zinc-100">
        <main className="py-8">
          {children}
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-50">
      <aside className="fixed inset-y-0 left-0 z-50 w-64 border-r border-zinc-200 bg-white shadow-sm">
        <div className="flex h-16 items-center border-b border-zinc-100 px-6">
          <div className="text-xl font-bold text-zinc-900">
            燃AI <span className="text-blue-600">1.0</span>
          </div>
        </div>
        <nav className="space-y-1.5 p-4">
          {navItems.map((item) => {
            const isActive = pathname === item.href || pathname?.startsWith(`${item.href}/`);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`group flex items-center gap-3 rounded-lg px-4 py-3 text-sm font-semibold transition-all duration-200 relative ${
                  isActive
                    ? "bg-blue-50/80 text-blue-700 shadow-sm"
                    : "text-zinc-600 hover:bg-zinc-50 hover:text-zinc-900"
                }`}
              >
                {/* Elegant active left border line */}
                {isActive && (
                  <div className="absolute left-0 top-1/4 h-1/2 w-1 rounded-r-md bg-blue-600" />
                )}
                <Icon
                  className={`h-4.5 w-4.5 transition-all duration-350 ${
                    isActive 
                      ? "text-blue-600 scale-105" 
                      : "text-zinc-400 group-hover:text-zinc-700 group-hover:scale-110"
                  }`}
                />
                {item.label}
                {item.href === "/documents" && <InterventionBadge />}
              </Link>
            );
          })}
        </nav>
        
        <div className="absolute bottom-0 left-0 right-0 border-t border-zinc-100 p-4 bg-white">
          <div className="flex items-center gap-2 text-xs font-semibold text-zinc-400">
            <div className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
            燃AI 内部运营系统 v1.0.0
          </div>
        </div>
      </aside>

      <main className="pl-64">
        <div className="mx-auto max-w-7xl px-8 py-8">
          {children}
        </div>
      </main>
    </div>
  );
}
